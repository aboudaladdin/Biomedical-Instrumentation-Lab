# Author : Sushmita
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks

# -----------------------------
# 1. Bandpass filter (0.1–0.5 Hz for respiration)
# -----------------------------
def bandpass_filter(signal, fs, low=0.1, high=0.5, order=3):
    nyquist = 0.5 * fs
    low_cut = low / nyquist
    high_cut = high / nyquist

    b, a = butter(order, [low_cut, high_cut], btype='band')
    filtered = filtfilt(b, a, signal)
    return filtered


# -----------------------------
# 2. Respiration rate estimation
# -----------------------------
def estimate_respiration_rate(ir_signal, fs):
    """
    ir_signal : raw IR PPG signal
    fs        : sampling frequency (Hz)
    """

    # Step 1: remove DC trend (baseline drift normalization)
    ir_detrended = ir_signal - np.mean(ir_signal)

    # Step 2: extract respiration component (low frequency envelope)
    resp_signal = bandpass_filter(ir_detrended, fs)

    # Step 3: smooth slightly (optional but helps stability)
    window = int(fs * 2)  # 2-second moving average
    if window > 1:
        kernel = np.ones(window) / window
        resp_signal = np.convolve(resp_signal, kernel, mode='same')

    # Step 4: detect peaks = breaths
    min_distance = int(fs * 2.5)  # minimum 2.5 sec between breaths (~24 bpm max)
    peaks, _ = find_peaks(resp_signal, distance=min_distance)

    # Step 5: compute respiration rate
    duration_sec = len(ir_signal) / fs
    breaths_per_min = (len(peaks) / duration_sec) * 60

    return breaths_per_min, resp_signal, peaks


