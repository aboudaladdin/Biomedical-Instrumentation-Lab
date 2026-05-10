import numpy as np


def calculate_spo2(red_signal, ir_signal):
    """
    Estimates blood oxygen level (SpO2) from red and infrared light signals.

    MAX30102 sensor shines red and IR light through your finger.
    More blood = more light absorbed. The signal goes up and down with each heartbeat.

    Split each signal into two parts:
        DC = the steady background level  --> use the average
        AC = the rise and fall each beat  --> use max minus min
    """

    # Step 1: Find the DC (background/baseline) for each signal
    # np.mean() adds all values and divides by count -- same as a regular average
    dc_red = np.mean(red_signal)
    dc_ir = np.mean(ir_signal)

    # Step 2: Find the AC (pulsatile swing) for each signal
    # This is simply how much the signal rises and falls each heartbeat
    ac_red = max(red_signal) - min(red_signal)
    ac_ir = max(ir_signal) - min(ir_signal)

    # Step 3: Safety check -- if either baseline is zero, something is wrong
    # (this would cause a 'divide by zero' crash in the next step)
    if dc_red == 0 or dc_ir == 0:
        print("Problem: baseline is zero. Is your finger on the sensor?")
        return 0

    # Step 4: Calculate R
    # R compares how much red vs infrared light is absorbed per pulse
    # High R = more red absorbed = less oxygen in blood
    # Low R  = more IR absorbed  = more oxygen in blood
    R = (ac_red / dc_red) / (ac_ir / dc_ir)

    # Step 5: Convert R into a SpO2 percentage
    # This formula comes from lab sheet
    spo2 = 110 - (25 * R)

    # Step 6: Check if the result makes physical sense
    # A healthy person is 95-100%. Below 70% the formula is not reliable.
    if spo2 < 70 or spo2 > 100:
        print(f'Result {spo2:.1f}% is outside the expected range (70-100%).')
        print("Try repositioning your finger and collecting data again.")
        return 0

    # round(spo2, 1) means round to 1 decimal place e.g. 97.3456 becomes 97.3
    return round(spo2, 1)
