"""
Real-time biomedical signal acquisition and monitoring system.

Acquires temperature (T1, T2), heart rate, and oxygen saturation data from Two thermistors and MAX30102
sensor via serial port. Processes signals to extract physiological metrics (HR, HRV, SpO2, PI)
and visualizes them in real-time. Sends periodic health metrics to remote monitoring server.
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
import requests
from datetime import datetime

# Global configuration
VISUALIZATION_UPDATE_WINDOW = 0.25  # seconds for visual updates (must be < metrics window)
DATA_SAMPLING_RATE = 50
received_timestamps = []

# Endpoint configuration
METRICS_WINDOW = 5  # seconds for metrics updates
last_send_time = 0  # Track last send time for throttling
base_url = "https://db65.uef.fi/api/v1/sepsis-session-data/update"
group_id = 2
session_id = "1O6SW5VD"
url = f"{base_url}/{group_id}/{session_id}"


def send_metrics(t1, t2, hr, hrv, spo2, pi):
    """
    Send physiological metrics to remote monitoring server.

    Validates that all values are within physiological ranges before sending.
    Throttles requests to METRICS_WINDOW interval to avoid overwhelming server.

    Args:
        t1: Core temperature (°C)
        t2: Extremity temperature (°C)
        hr: Heart rate (BPM)
        hrv: Heart rate variability - RMSSD (ms)
        spo2: Oxygen saturation (%)
        pi: Perfusion index (%)
    """
    global last_send_time

    current_time = time.time()

    # Check if enough time has passed since last send
    if current_time - last_send_time < METRICS_WINDOW:
        return

    # Validate values are within physiological ranges
    valid_t1 = 10 <= t1 <= 50
    valid_t2 = 10 <= t2 <= 50
    valid_hr = 30 <= hr <= 200
    valid_hrv = 1 <= hrv <= 110
    valid_pi = 0.1 <= pi <= 20
    valid_spo2 = 70 <= spo2 <= 100

    # Only send if all values are valid
    if not (valid_t1 and valid_t2 and valid_hr and valid_hrv and valid_pi and valid_spo2):
        print(f"⚠ Invalid metrics (skipping send): T1={t1} T2={t2} HR={hr} HRV={hrv} PI={pi} SpO2={spo2}")
        return

    payload = {
        "t1": t1,                           # core_temperature
        "t2": t2,                           # extremity_temperature
        "delta_t": round(t1 - t2),          # temperature difference
        "spo2": spo2,                       # oxygen saturation
        "hr": hr,                           # heart_rate
        "resp_rate": 16,                    # respiration_rate (not implemented yet)
        "hrv_rmssd": hrv,                   # hrv_in_rmssd
        "perfusion_index": pi,              # pi
        "timestamp": datetime.now().isoformat()  # current timestamp
    }

    # Make the PUT request
    try:
        response = requests.put(url, json=payload)

        # Print results
        print(f"URL: {url}")
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {response.headers}")
        print(f"Response Body: {response.text}")

        # Check if request was successful
        if response.status_code in [200, 201, 204]:
            print("\n✓ Request successful!")

        else:
            print("\n✗ Request failed!")

        last_send_time = current_time

    except requests.exceptions.RequestException as e:
        print(f"Error making request: {e}")


def calculate_hr(peak_indices, sampling_rate=50):
    """
    Calculate heart rate from detected R-peak intervals.

    HR = 60 / mean(RR interval in seconds)

    Args:
        peak_indices: Array indices where heartbeats are detected
        sampling_rate: Data sampling rate in Hz (default 50)

    Returns:
        Heart rate in BPM, or 0 if insufficient peaks
    """
    if len(peak_indices) < 2:
        return 0
    peak_times = np.array(peak_indices) / sampling_rate
    intervals = np.diff(peak_times)
    return 60 / np.mean(intervals) if np.mean(intervals) > 0 else 0


def calculate_hrv(peak_indices, sampling_rate=50):
    """
    Calculate heart rate variability using RMSSD (Root Mean Square of Successive Differences).

    Measures variability between consecutive RR intervals, indicator of autonomic nervous system activity.

    Args:
        peak_indices: Array indices where heartbeats are detected
        sampling_rate: Data sampling rate in Hz (default 50)

    Returns:
        HRV in milliseconds, or 0 if insufficient peaks
    """
    if len(peak_indices) < 2:
        return 0
    peak_times = np.array(peak_indices) / sampling_rate
    intervals = np.diff(peak_times) * 1000  # convert to ms
    diff_intervals = np.diff(intervals)
    return np.sqrt(np.mean(diff_intervals**2))


def calculate_pi(raw_data, window_samples=1000):
    """
    Calculate perfusion index from raw optical signal.

    PI = (AC / DC) * 100, where AC = pulsatile component, DC = mean signal.
    Indicates quality of peripheral perfusion.

    Args:
        raw_data: Raw IR signal samples
        window_samples: Number of recent samples to analyze (default 1000)

    Returns:
        Perfusion index as percentage, or 0 if insufficient data
    """
    if len(raw_data) < window_samples:
        return 0
    recent = np.array(raw_data[-window_samples:])
    dc = np.mean(recent)
    ac = np.sqrt(np.mean((recent - dc)**2))
    return (ac / dc) * 100 if dc > 0 else 0


def calculate_spo2(red_data, ir_data, window=1000):
    """
    Calculate oxygen saturation from red and infrared absorption ratios.

    SpO2 = 110 - 25*R, where R = (AC_red/DC_red) / (AC_ir/DC_ir).
    Based on different light absorption characteristics of oxygenated vs deoxygenated hemoglobin.

    Args:
        red_data: Red channel samples
        ir_data: Infrared channel samples
        window: Number of recent samples to analyze (default 1000)

    Returns:
        SpO2 as percentage (70-100 range), or 0 if insufficient data
    """
    # ratio of ratios: PI_red / PI_infrared
    if len(red_data) < window or len(ir_data) < window:
        return 0
    red_recent = np.array(red_data[-window:])
    ir_recent = np.array(ir_data[-window:])
    ac_red = np.std(red_recent)
    dc_red = np.mean(red_recent)
    ac_ir = np.std(ir_recent)
    dc_ir = np.mean(ir_recent)

    # Protect against division by zero
    if dc_red > 0 and dc_ir > 0 and ac_ir > 0 and ac_red > 0:
        R = (ac_red / dc_red) / (ac_ir / dc_ir)
    else:
        R = 0
    spo2 = 110 - 25 * R
    return max(70, min(100, spo2))  # Clamp to valid range


def convolve(signal, filter=[1, 1]):
    """
    Apply 1D convolution filter to signal (moving average-like operation).

    Manual implementation of np.convolve with normalized kernel.
    Used for noise reduction and signal smoothing.

    Args:
        signal: Input signal array
        filter: Convolution kernel (default [1,1] = moving average)

    Returns:
        Filtered signal with same length as input
    """

    signal = np.array(signal)
    filter = np.array(filter)
    filter_length = len(filter)
    signal_length = len(signal)

    # Safety fallback for short signals
    if signal_length < filter_length:
        return signal

    filter_sum = np.sum(np.abs(filter))
    # Pad signal at beginning to preserve initial samples
    padded_signal = np.pad(signal, (filter_length, 0), mode='edge')

    filtered_signal = np.zeros(signal_length)

    # Slide filter across signal
    for i in range(signal_length):
        window = padded_signal[i : i + filter_length]
        filtered_signal[i] = np.dot(window, filter) / filter_sum

    return filtered_signal


def peak_detector(signal, min_time=10, min_threshold=1):
    """
    Detect local maxima (heartbeat peaks) in signal with temporal and amplitude constraints.

    Finds peaks (local maxima) and filters by minimum peak spacing (refractory period)
    and minimum amplitude threshold.

    Args:
        signal: Input signal
        min_time: Minimum sample distance between peaks (default 10 = ~200ms at 50Hz)
        min_threshold: Minimum peak value to be considered (default 1)

    Returns:
        Tuple of (peak_indices, peak_values) arrays
    """

    indices = []
    values = []

    # Scan for local maxima
    for i in range(1, len(signal)-1):
        if signal[i] < min_threshold:
            continue
        if (signal[i] > signal[i-1]) and (signal[i] > signal[i+1]):
            # Local maxima found - check refractory period
            if len(indices) > 0:
                last_beat_time = indices[-1]
                current_beat_time = i
                if current_beat_time - last_beat_time > min_time:
                    indices.append(i)
                    values.append(signal[i])
            else:
                indices.append(i)
                values.append(signal[i])
    return indices, values


# ============ Serial Communication & Real-Time Visualization ============

def select_port():
    """
    Prompt user to select serial port from available options.

    Returns:
        Device name of selected port (e.g., 'COM3')
    """
    ports = list(serial.tools.list_ports.comports())
    for i, p in enumerate(ports):
        print(f"[{i}] {p.device}")
    sel = int(input("Select Port: "))
    return ports[sel].device


SERIAL_PORT = select_port()
BAUD_RATE = 115200

# Data buffers for signal analysis and visualization
ir_buffer = deque(maxlen=1000)  # IR data for metrics calculation
red_buffer = deque(maxlen=1000)  # Red data for metrics calculation

# Visualization buffers (shorter for responsive updates)
visual_buffer = deque(maxlen=200)  # IR data for plotting
t1_buffer = deque(maxlen=200)  # Core temperature for plotting
t2_buffer = deque(maxlen=200)  # Extremity temperature for plotting

# Finger detection state
finger_present = False
finger_threshold = 10000  # IR signal strength threshold for finger detection


def serial_reader():
    """
    Background thread that reads sensor data from serial port.

    Parses comma-separated values: T1,T2,Red,IR
    Populates data buffers and detects when finger is on sensor.
    Updates timestamp list for tracking data rate.
    """
    global finger_present
    with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as ser:
        while True:
            try:
                line = ser.readline().decode('utf-8').strip()
                if line:
                    parts = line.split(',')
                    if len(parts) == 4:
                        t1 = float(parts[0])
                        t2 = float(parts[1])
                        red = float(parts[2])
                        ir = float(parts[3])

                        # Always buffer temperature
                        t1_buffer.append(t1)
                        t2_buffer.append(t2)

                        # Only buffer optical signals when finger is detected
                        finger_present = ir > finger_threshold
                        if finger_present:
                            ir_buffer.append(ir)
                            red_buffer.append(red)
                            visual_buffer.append(ir)
                            received_timestamps.append(time.time())
                            # Maintain 5-second rolling window of timestamps
                            cutoff = time.time() - 5
                            received_timestamps[:] = [t for t in received_timestamps if t > cutoff]

            except (ValueError, UnicodeDecodeError):
                # Skip malformed lines
                pass


# Start background serial reader thread
thread = threading.Thread(target=serial_reader, daemon=True)
thread.start()


# ============ Matplotlib Real-Time Visualization ============

# Figure layout
fig = plt.figure(figsize=(16, 10))
gs = gridspec.GridSpec(2, 1, height_ratios=[0.8, 0.2], hspace=0.25)

ax1 = fig.add_subplot(gs[0])  # Filtered signal and metrics
ax2 = fig.add_subplot(gs[1], sharex=ax1)  # Raw signal

# Clinical theme colors
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

# Plot objects
line_filtered, = ax1.plot([], [], color=FILTERED_COLOR, linewidth=2, label='Cleaned')
scatter_peaks = ax1.scatter([], [], color='red', s=50, marker='o', alpha=0.7, label='Heartbeat')
line_raw, = ax2.plot([], [], color=RAW_COLOR, linewidth=1, label='Raw IR')

# Finger status indicator
finger_text = ax1.text(0.02, 0.98, '', transform=ax1.transAxes, fontsize=22,
                      verticalalignment='top', bbox=dict(boxstyle='round', facecolor='green', alpha=0.5))

metrics_text_obj = None


def animate(frame):
    """
    Animation callback: Process signal, detect beats, calculate metrics, and update plots.

    Called at ~4Hz (250ms interval). Applies multi-stage signal filtering,
    detects heartbeat peaks, calculates physiological metrics, and sends to server.
    """
    global metrics_text_obj
    raw_list = np.array(list(visual_buffer))  # Use visual buffer for plotting

    if not finger_present or len(raw_list) < 10:
        # Clear plots and show status message
        line_filtered.set_data([], [])
        scatter_peaks.set_offsets(np.empty((0, 2)))
        line_raw.set_data([], [])
        finger_text.set_text('Place finger on sensor')
        finger_text.set_backgroundcolor('red')
        finger_text.set_fontsize(22)
        finger_text.set_alpha(0.5)
        return line_filtered, scatter_peaks, line_raw, finger_text

    # Finger present - process signal
    finger_text.set_text('Finger detected - Processing...')
    finger_text.set_backgroundcolor('green')
    finger_text.set_fontsize(12)
    finger_text.set_alpha(0.1)

    # Multi-stage signal filtering:
    # 1. Smooth with [1,1,1] moving average
    # 2. Derivative with [1,0,-1] edge detector
    # 3. Smooth again with longer kernel
    filtered = convolve(raw_list, filter=[1, 1, 1])
    filtered = convolve(filtered, filter=[1, 0, -1])
    filtered = convolve(filtered, filter=[1, 1, 1, 1, 1])

    # Detect peaks adaptively (20% of max signal)
    max_value = np.max(filtered) if len(filtered) > 0 else 0
    beat_indices, beat_values = peak_detector(filtered, min_time=15, min_threshold=max_value*0.2)

    # Update plots
    x_data = np.arange(len(raw_list))
    line_filtered.set_data(x_data, filtered)
    scatter_peaks.set_offsets(np.column_stack((beat_indices, beat_values)))
    line_raw.set_data(x_data, raw_list)

    # Calculate physiological metrics
    t1 = np.mean(list(t1_buffer))
    t2 = np.mean(list(t2_buffer))
    hr = calculate_hr(beat_indices, sampling_rate=DATA_SAMPLING_RATE)
    hrv = calculate_hrv(beat_indices, sampling_rate=DATA_SAMPLING_RATE)
    pi = calculate_pi(list(ir_buffer), 100)
    spo2 = calculate_spo2(list(red_buffer), list(ir_buffer), 100)

    # Format metrics: show "--" if outside physiological range
    t1_display = f"{t1:.1f}" if 10 <= t1 <= 50 else "--"
    t2_display = f"{t2:.1f}" if 10 <= t2 <= 50 else "--"
    hr_display = f"{hr:.1f}" if 30 <= hr <= 200 else "--"
    hrv_display = f"{hrv:.1f}" if 1 <= hrv <= 110 else "--"
    pi_display = f"{pi:.1f}" if 0.1 <= pi <= 20 else "--"
    spo2_display = f"{spo2:.1f}" if 70 <= spo2 <= 100 else "--"

    # Send to remote monitoring server
    send_metrics(t1, t2, hr, hrv, spo2, pi)

    # Calculate actual data sampling rate
    current_time = time.time()
    recent_samples = [t for t in received_timestamps if current_time - t <= 1.0]
    sampling_rate = len(recent_samples)

    # Format metrics display
    metrics_text = f'T1: {t1_display} C\n,T2: {t2_display} C\nHR: {hr_display} BPM\nHRV: {hrv_display} ms\nPI: {pi_display}%\nSpO2: {spo2_display}%\nData Rate: {sampling_rate} Hz'

    # Remove old metrics text box
    if metrics_text_obj is not None:
        metrics_text_obj.remove()

    # Add updated metrics text
    metrics_text_obj = ax1.text(0.85, 0.95, metrics_text, transform=ax1.transAxes, fontsize=15,
                                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7))

    # Auto-scale axes with margin to prevent visualization issues
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


# Create animation at 4 Hz (250ms interval)
ani = FuncAnimation(fig, animate, interval=VISUALIZATION_UPDATE_WINDOW * 1000, blit=False, cache_frame_data=False)

if __name__ == '__main__':
    plt.tight_layout()
    plt.show()
