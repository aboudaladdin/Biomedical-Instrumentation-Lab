"""
Real-time MAX30102 Pulse Oximeter Signal Processing and Visualization

Reads IR and Red LED data from a MAX30102 sensor via serial connection,
processes the signal with filtering and peak detection, calculates cardiovascular
metrics (HR, HRV, RR, PI, SpO2), and displays real-time visualization.
Author: Abdelrahman Arafa
Notes: AI assisted in comments, docstring, creating boilerplate for visualization and serial communications
"""

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import matplotlib.gridspec as gridspec

import serial
import serial.tools.list_ports
import threading
from collections import deque
import numpy as np
import time
from spo2_karthika import calculate_spo2
from respiration_rate_sushmita import estimate_respiration_rate

# Global configuration
METRICS_WINDOW = 5  # seconds for metrics calculation
DATA_SAMPLING_RATE = 50
received_timestamps = []

# ============================================================================
# METRICS CALCULATION FUNCTIONS
# ============================================================================

def calculate_rr(peak_indices, sampling_rate=50):
    """
    Calculate respiratory rate (RR) - average interval between heartbeats in milliseconds.

    :param peak_indices: Array indices of detected peaks
    :param sampling_rate: Hz, default 50
    :return: Mean interval between peaks in milliseconds
    """
    if len(peak_indices) < 2: return 0
    peak_times = np.array(peak_indices) / sampling_rate # to get peak in seconds each datapoint is (1/200) seconds
    intervals = np.diff(peak_times) * 1000  # to ms
    return np.mean(intervals)

def calculate_hr(peak_indices, sampling_rate=50):
    """
    Calculate heart rate (HR) - beats per minute.

    :param peak_indices: Array indices of detected peaks
    :param sampling_rate: Hz, default 50
    :return: Heart rate in BPM
    """
    if len(peak_indices) < 2: return 0
    peak_times = np.array(peak_indices) / sampling_rate # to get peak in seconds each datapoint is (1/200) seconds
    intervals = np.diff(peak_times)
    return 60 / np.mean(intervals) if np.mean(intervals) > 0 else 0

def calculate_hrv(peak_indices, sampling_rate=50):
    """
    Calculate heart rate variability (HRV) - variability of intervals between peaks.
    Uses RMSSD: root mean square of successive differences.

    :param peak_indices: Array indices of detected peaks
    :param sampling_rate: Hz, default 50
    :return: HRV in milliseconds (RMSSD)
    """
    if len(peak_indices) < 2: return 0
    peak_times = np.array(peak_indices) / sampling_rate # to get peak in seconds each datapoint is (1/200) seconds
    intervals = np.diff(peak_times) * 1000  # to ms
    diff_intervals = np.diff(intervals)
    return np.sqrt(np.mean(diff_intervals**2))

def calculate_pi(raw_data, window_samples=1000):
    """
    Calculate perfusion index (PI) - ratio of AC to DC components of the signal.
    Indicates signal quality and finger placement.

    :param raw_data: Raw signal values
    :param window_samples: Number of recent samples to analyze
    :return: PI as percentage (AC/DC * 100)
    """
    if len(raw_data) < window_samples: return 0
    recent = np.array(raw_data[-window_samples:])
    dc = np.mean(recent)
    ac = np.sqrt(np.mean((recent - dc)**2))
    return (ac / dc) * 100 if dc > 0 else 0


# ============================================================================
# SIGNAL FILTERING FUNCTIONS
# ============================================================================

def convolve(signal, filter:list=[1, 1]):
    """
    Custom convolution filter for signal processing (manual implementation of moving average).

    Applies a linear filter to the signal using edge padding for boundary handling.
    Implements: np.convolve(signal, filter / np.sum(filter), mode='same')

    :param signal: Input signal array
    :param filter: Filter coefficients, defaults to [1, 1] for moving average
    :type filter: list
    :return: Filtered signal array
    """

    signal = np.array(signal)
    filter = np.array(filter)
    filter_length = len(filter)
    signal_length = len(signal)

    # safety fallback - if signal shorter than filter, return as-is
    if signal_length < filter_length:
        return signal

    filter_sum = np.sum(np.abs(filter))
    padded_signal = np.pad(signal, (filter_length, 0), mode='edge')

    filtered_signal = np.zeros(signal_length)

    # Slide filter across signal
    for i in range(signal_length):
        window = padded_signal[i : i + filter_length]
        filtered_signal[i] = np.dot(window, filter) / filter_sum

    return filtered_signal

def peak_detector(signal, min_time=10, min_threshold=1):
    """
    Detect local maxima (peaks) in signal with temporal and amplitude constraints.

    Identifies heartbeat peaks by finding local maxima and filtering based on:
    - Minimum time between peaks (prevents false positives)
    - Minimum signal amplitude threshold

    :param signal: Input signal array
    :param min_time: Minimum samples between consecutive peaks (prevents jitter)
    :param min_threshold: Minimum peak amplitude value
    :return: (peak_indices, peak_values) - lists of peak locations and amplitudes
    """

    indices = []
    values = []
    for i in range(1, len(signal)-1):
        if signal[i] < min_threshold:
            continue
        if (signal[i] > signal[i-1]) and (signal[i] > signal[i+1]):
            # local maxima found - check time constraint
            if len(indices) > 0:
                last_beat_time = indices[-1]
                current_beat_time = i
                if current_beat_time - last_beat_time > min_time:
                    indices.append(i)
                    values.append(signal[i])
            else:
                # First peak
                indices.append(i)
                values.append(signal[i])
    return indices, values


# ============================================================================
# SERIAL PORT COMMUNICATION
# ============================================================================

def select_port():
    """
    Interactive serial port selection from available COM ports.

    :return: Selected COM port device string
    """
    ports = list(serial.tools.list_ports.comports())
    for i, p in enumerate(ports): print(f"[{i}] {p.device}")
    sel = int(input("Select Port: "))
    return ports[sel].device

SERIAL_PORT = select_port()
BAUD_RATE = 115200

# Buffers for signal analysis (larger window for metric calculations)
ir_buffer = deque(maxlen=1000)  # IR data for analysis
red_buffer = deque(maxlen=1000)  # Red data for analysis

# Buffer for visualization (shorter window for real-time display performance)
visual_buffer = deque(maxlen=200)  # IR data for plotting

finger_present = False
finger_threshold = 10000

def serial_reader():
    """
    Background thread routine to read MAX30102 data from serial port.

    Continuously reads CSV-formatted data (red, IR values), validates finger presence,
    and populates data buffers. Runs as daemon thread to avoid blocking animation.

    Data format: TIMESTAMP,TEMPERATURE,RED,IR
    """
    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
        while True:
            try:
                line = ser.readline().decode('utf-8').strip()
                if line:

                        parts = line.split(',')
                        if len(parts) == 4:
                            red = float(parts[2])
                            ir = float(parts[3])

                            global finger_present
                            # Determine if finger is present based on IR signal strength
                            finger_present = ir > finger_threshold
                            if finger_present:
                                ir_buffer.append(ir)
                                red_buffer.append(red)
                                visual_buffer.append(ir)
                                received_timestamps.append(time.time())
                                # Clean old timestamps (keep last 5 seconds)
                                cutoff = time.time() - 5
                                received_timestamps[:] = [t for t in received_timestamps if t > cutoff]

            except (ValueError, UnicodeDecodeError):
                pass

# Start the serial reader thread
thread = threading.Thread(target=serial_reader, daemon=True)
thread.start()

# ============================================================================
# MATPLOTLIB VISUALIZATION SETUP
# ============================================================================

fig = plt.figure(figsize=(16, 10))
gs = gridspec.GridSpec(2, 1, height_ratios=[0.8, 0.2], hspace=0.25)

ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1], sharex=ax1)

# Clinical theme color palette
BG_COLOR = "#fefefe"
GRID_COLOR = "#D9D2CC"
RAW_COLOR = "#8E8E8E"
FILTERED_COLOR = "#56473B"

fig.patch.set_facecolor(BG_COLOR)
ax1.set_facecolor(BG_COLOR)
ax2.set_facecolor(BG_COLOR)

ax1.grid(True, color=GRID_COLOR, linewidth=1)
ax2.grid(True, color=GRID_COLOR, linewidth=1)

ax1.set_title("Filtered IR Signal", fontsize=14, fontweight='bold')
ax2.set_title("RAW IR SIGNAL", fontsize=12)

# Initialize plot line and scatter objects
line_filtered, = ax1.plot([], [], color=FILTERED_COLOR, linewidth=2, label='Cleaned')
scatter_peaks = ax1.scatter([], [], color='red', s=50, marker='o', alpha=0.7, label='Heartbeat')
line_raw, = ax2.plot([], [], color=RAW_COLOR, linewidth=1, label='Raw IR')

# Text annotation for finger status
finger_text = ax1.text(0.02, 0.98, '', transform=ax1.transAxes, fontsize=22,
                      verticalalignment='top', bbox=dict(boxstyle='round', facecolor='green', alpha=0.5))

metrics_text_obj = None

def animate(frame):
    """
    Animation callback - updates plots and metrics on each frame.

    Processing pipeline:
    1. Apply multi-stage signal filtering (moving average, derivative, squaring)
    2. Detect heartbeat peaks using peak_detector
    3. Calculate all cardiovascular metrics (HR, HRV, RR, PI, SpO2)
    4. Update visualization and display metrics

    :param frame: Animation frame number (not used)
    :return: Tuple of artists to update on screen
    """
    global metrics_text_obj
    raw_list = np.array(list(visual_buffer))  # Use visual buffer for plotting
    #raw_list =  -raw_list + np.max(raw_list)  # valleys are systolic peaks

    # No data or finger not detected - display prompt
    if not finger_present or len(raw_list) < 10:
        line_filtered.set_data([], [])
        scatter_peaks.set_offsets(np.empty((0, 2)))
        line_raw.set_data([], [])
        finger_text.set_text('Place finger on sensor')
        finger_text.set_backgroundcolor('red')
        finger_text.set_fontsize(22)
        finger_text.set_alpha(0.5)
        return line_filtered, scatter_peaks, line_raw, finger_text

    # Finger detected - process signal with multi-stage filter pipeline
    finger_text.set_text('Finger detected - Processing...')
    finger_text.set_backgroundcolor('green')
    finger_text.set_fontsize(12)
    finger_text.set_alpha(0.1)

    # Signal processing pipeline: MA filter -> derivative -> squaring -> MA filter
    filtered = convolve(raw_list, filter=[1, 1])
    filtered = convolve(filtered, filter=[1, 0, -1])
    filtered = np.power(filtered, 2)
    filtered = convolve(filtered, filter=[1, 1, 1, 1, 1])

    # Detect peaks with adaptive threshold (35% of max)
    max_value = np.max(filtered) if len(filtered) > 0 else 0
    beat_indices, beat_values = peak_detector(filtered, min_time=14, min_threshold=max_value*0.35)

    # Update plot lines and scatter
    x_data = np.arange(len(raw_list))
    line_filtered.set_data(x_data, filtered)
    scatter_peaks.set_offsets(np.column_stack((beat_indices, beat_values)))
    line_raw.set_data(x_data, raw_list)

    # Calculate all cardiovascular metrics
    rr = calculate_rr(beat_indices, sampling_rate=DATA_SAMPLING_RATE)
    hr = calculate_hr(beat_indices, sampling_rate=DATA_SAMPLING_RATE)
    hrv = calculate_hrv(beat_indices, sampling_rate=DATA_SAMPLING_RATE)
    pi = calculate_pi(list(ir_buffer), 100)
    spo2 = calculate_spo2(list(red_buffer), list(ir_buffer))
    resp_rate,_,_ = estimate_respiration_rate(list(ir_buffer),fs=DATA_SAMPLING_RATE)
    
    # Validate metrics against physiological ranges, show "--" if out of range
    rr_display = f"{rr:.1f}" if 300 <= rr <= 2000 else "--"
    hr_display = f"{hr:.1f}" if 30 <= hr <= 200 else "--"
    hrv_display = f"{hrv:.1f}" if 1 <= hrv <= 110 else "--"
    pi_display = f"{pi:.1f}" if 0.1 <= pi <= 20 else "--"
    spo2_display = f"{spo2:.1f}" if 70 <= spo2 <= 100 else "--"
    resp_rate_display = f"{resp_rate:.1f}" if 9 <= resp_rate <= 30 else "--"
    
    # Calculate actual sampling rate from timestamps
    current_time = time.time()
    recent_samples = [t for t in received_timestamps if current_time - t <= 1.0]
    sampling_rate = len(recent_samples)

    # Format metrics text display
    metrics_text = f'RR: {rr_display} ms\nHR: {hr_display} BPM\nHRV: {hrv_display} ms\nPI: {pi_display}%\nSpO2: {spo2_display}%\nRespirationRate: {resp_rate_display}%\nData Rate: {sampling_rate} Hz'

    # Update metrics text box
    if metrics_text_obj is not None:
        metrics_text_obj.remove()
    metrics_text_obj = ax1.text(0.89, 0.95, metrics_text, transform=ax1.transAxes, fontsize=15,
                               verticalalignment='top', bbox=dict(boxstyle='round', facecolor='grey', alpha=0.7))

    # Auto-scale y-axes with margin to prevent singular transformation
    y_min_filtered = np.min(filtered)
    y_max_filtered = np.max(filtered)
    y_margin_filtered = max(1, (y_max_filtered - y_min_filtered) * 0.1)

    y_min_raw = np.min(raw_list)
    y_max_raw = np.max(raw_list)
    y_margin_raw = max(1, (y_max_raw - y_min_raw) * 0.1)

    ax1.set_xlim(0, len(raw_list))
    ax1.set_ylim(y_min_filtered - y_margin_filtered, y_max_filtered + y_margin_filtered)
    ax2.set_xlim(0, len(raw_list))
    ax2.set_ylim(y_min_raw - y_margin_raw, y_max_raw + y_margin_raw)

    return line_filtered, scatter_peaks, line_raw, finger_text

# Create animation with 250ms update interval
ani = FuncAnimation(fig, animate, interval=250, blit=False, cache_frame_data=False)

if __name__ == '__main__':
    plt.tight_layout()
    plt.show()
