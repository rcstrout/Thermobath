"""
Modernized Cole Parmer Thermobath Controller
- Python 3 syntax
- Modular functions
- Improved error handling
- Configurable serial port
- Logging and documentation
- Stepwise temperature profile routine for paraffin flow loop
"""

import time
import serial
import sys
import os
import csv
from pathlib import Path

import sys
import os

# Add the project root to the Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from thermobath import core as thermobath_core


def get_serial_port():
    """Prompt user for serial port, default to COM3."""
    port = input("Enter serial port (e.g., COM3) [default: COM3]: ").strip()
    return port if port else "COM3"

def connect_bath(port):
    """Establish serial connection to the bath."""
    try:
        bath = serial.Serial(
            port,
            baudrate=19200,
            bytesize=8,
            stopbits=1,
            timeout=1
        )
        print("***********************************")
        print(f"Serial connection established on {bath.name}")
        print("***********************************")
        time.sleep(2)
        bath.write(b"SE1\r")  # turn on command echo
        response = bath.readline()
        print(f"SE1 response: {response.decode().strip()}")
        bath.write(b"RT\r")
        response = bath.readline()
        print(f"Current bath temperature: {response.decode().strip()}")
        bath.write(b"RS\r")
        response = bath.readline()
        print(f"Current bath setpoint: {response.decode().strip()}")
        return bath
    except serial.SerialException as e:
        print("++++++++++++++++++++++++++")
        print(f"Serial connection failed: {e}")
        print("++++++++++++++++++++++++++")
        time.sleep(5)
        return None

def prompt_float(prompt_text):
    """Prompt for a float value."""
    while True:
        try:
            return float(input(prompt_text))
        except ValueError:
            print("Please enter a valid number.")


def prompt_yes_no(prompt_text, default="y"):
    """Prompt user for a yes/no answer (returns True for yes)."""
    default = default.lower()[0] if default else "y"
    while True:
        resp = input(f"{prompt_text} [y/n] (default: {default}): ").strip().lower()
        if not resp:
            resp = default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'.")




def set_constant_temperature(bath, set_temp=None):
    """Set and hold a constant temperature."""
    if set_temp is None:
        set_temp = prompt_float("Enter the constant setpoint temperature (C): ")
    print(f"Setting constant temperature: {set_temp:.2f} C")
    bath.write(b"SO 1\r")
    bath.readline()
    command = f"SS {set_temp:.2f}\r".encode('utf-8')
    bath.write(command)
    bath.readline()
    time.sleep(10)
    bath.write(b"RS\r")
    response = bath.readline()
    try:
        new_point = float(response[:5])
        if abs(new_point - set_temp) < 0.1:
            print(f"Setpoint set: {new_point:.2f} C")
        else:
            print(f"Setpoint mismatch: {new_point:.2f} C (expected {set_temp:.2f} C)")
    except Exception:
        print("Could not parse setpoint response.")

def wait_for_temperature(bath, target_temp, tolerance=0.1, poll_interval=5):
    """Wait until bath reaches target temperature within tolerance."""
    while True:
        time.sleep(poll_interval)
        bath.write(b"RT\r")
        response = bath.readline()
        try:
            current_temp = float(response[:5])
            print(f"Current bath temp: {current_temp:.2f} C")
            if abs(current_temp - target_temp) < tolerance:
                break
        except Exception:
            print("Could not parse temperature response.")

def ramp_temperature(bath, start_temp, target_temp, rate_c_per_hr):
    """Ramp temperature from start_temp to target_temp at given rate."""
    temp_diff = target_temp - start_temp
    ramp_duration_hr = abs(temp_diff) / rate_c_per_hr if rate_c_per_hr != 0 else 0
    ramp_duration_min = ramp_duration_hr * 60
    print(f"Ramp will take {ramp_duration_hr:.2f} hrs ({ramp_duration_min:.0f} minutes)")
    rate_c_per_min = rate_c_per_hr / 60 if rate_c_per_hr != 0 else 0
    if temp_diff < 0:
        rate_c_per_min *= -1
    current_set = start_temp
    bath.write(b"SO 1\r")
    bath.readline()
    while (rate_c_per_min > 0 and current_set < target_temp) or (rate_c_per_min < 0 and current_set > target_temp):
        command = f"SS {current_set:.2f}\r".encode('utf-8')
        bath.write(command)
        bath.readline()
        print(f"Setpoint updated: {current_set:.2f} C")
        time.sleep(60)
        current_set += rate_c_per_min
        # Prevent overshooting
        if (rate_c_per_min > 0 and current_set > target_temp) or (rate_c_per_min < 0 and current_set < target_temp):
            current_set = target_temp
    # Final setpoint
    command = f"SS {target_temp:.2f}\r".encode('utf-8')
    bath.write(command)
    bath.readline()
    print("Waiting to reach final temperature...")
    wait_for_temperature(bath, target_temp, tolerance=0.05, poll_interval=5)

def hold_temperature(
    bath,
    hold_time_min,
    pressure_reader=None,
    pressure_channel=None,
    pressure_max=None,
    check_interval=30,
):
    """Hold current temperature for specified minutes.

    Optionally watches a pressure channel (from a WinDAQ log) and extends the hold
    if pressure exceeds a configured maximum.
    """
    print(f"Holding temperature for {hold_time_min:.0f} minutes.")
    pressure_source = thermobath_core.ensure_pressure_source(pressure_reader)
    end_time = time.time() + hold_time_min * 60
    while time.time() < end_time:
        time.sleep(check_interval)

        if pressure_source is not None and pressure_channel is not None:
            try:
                pressure = pressure_source.read_channel(pressure_channel)
            except Exception as e:
                pressure = None
                print(f"Warning: could not read pressure (reader error): {e}")

            if pressure is not None:
                print(f"Pressure (from log): {pressure:.3f}")
                if pressure_max is not None and pressure > pressure_max:
                    print(
                        f"Pressure {pressure:.3f} > {pressure_max:.3f}: extending hold and waiting for pressure to drop..."
                    )
                    end_time = max(end_time, time.time()) + check_interval
                    continue

    # Optionally, print status or check bath status here


def stepwise_profile(bath, cloud_point, step_size, hold_time, min_temp, initial_overheat=10):
    """
    Run a stepwise temperature profile:
    1. Heat to (cloud_point + initial_overheat)
    2. Wait for user to dose products
    3. Ramp down to cloud_point
    4. Stepwise drop by step_size, holding at each for hold_time
    5. End at min_temp or when user stops
    """
    print("\n--- Stepwise Temperature Profile Routine ---")

    # Optional: use a WinDAQ log file to monitor pressure and intelligently extend hold periods
    use_windaq = prompt_yes_no(
        "Monitor pressure via WinDAQ log file to pause/extend holds?", default="n"
    )
    pressure_reader = None
    pressure_channel = None
    pressure_max = None

    if use_windaq:
        log_file = input("Enter WinDAQ log file path: ").strip()
        delimiter = input("Enter log delimiter (default ,): ").strip() or ","
        has_header = prompt_yes_no("Does the log file have a header row?", default="y")
        pressure_reader = thermobath_core.WinDAQPressureSource(
            log_file, delimiter=delimiter, has_header=has_header
        )

        channel_input = input(
            "Enter pressure channel(s) as comma-separated names or 1-based indices (default 1): "
        ).strip() or "1"
        parts = [p.strip() for p in channel_input.split(",") if p.strip()]
        if len(parts) == 1 and parts[0].isdigit():
            pressure_channel = int(parts[0]) - 1
        else:
            parsed = []
            for part in parts:
                if part.isdigit():
                    parsed.append(int(part) - 1)
                else:
                    parsed.append(part)
            pressure_channel = parsed

        pressure_max = prompt_float("Enter maximum allowable pressure (units as in log): ")

    overheat_temp = cloud_point + initial_overheat
    print(f"Step 1: Heating to {overheat_temp:.2f} C (cloud point + {initial_overheat} C)")
    set_constant_temperature(bath, overheat_temp)
    wait_for_temperature(bath, overheat_temp)
    print("Reached overheat temperature.")
    input("Dose products now, then press Enter to continue...")

    print(f"Step 2: Ramping down to cloud point ({cloud_point:.2f} C)")
    ramp_temperature(bath, overheat_temp, cloud_point, rate_c_per_hr=5)  # Default ramp rate, adjust as needed
    print("Reached cloud point.")

    current_temp = cloud_point
    while current_temp > min_temp:
        next_temp = max(current_temp - step_size, min_temp)
        print(f"Step 3: Ramping down to {next_temp:.2f} C")
        ramp_temperature(bath, current_temp, next_temp, rate_c_per_hr=5)  # Default ramp rate, adjust as needed
        print(f"Holding at {next_temp:.2f} C for {hold_time:.0f} minutes.")
        hold_temperature(
            bath,
            hold_time,
            pressure_reader=pressure_reader,
            pressure_channel=pressure_channel,
            pressure_max=pressure_max,
        )
        current_temp = next_temp
        # In future: check pressure transducer here to break if needed
        # For now, continue until min_temp is reached

    print("Stepwise profile complete. Minimum temperature reached.")

def main():
    port = get_serial_port()
    bath = connect_bath(port)
    if not bath:
        sys.exit(1)

    print("######################################################")
    print("Choose a routine to run (enter 0, 1, 2, or 3):")
    print("0. Set to constant temperature")
    print("1. Set to starting temperature, pause, then start ramp")
    print("2. Start ramp immediately from current temperature")
    print("3. Stepwise temperature profile for paraffin flow loop")
    prog = input("Enter 0, 1, 2, or 3: ").strip()

    if prog == "0":
        set_constant_temperature(bath)
    elif prog == "1":
        init_temp = prompt_float("Enter the starting temperature (C): ")
        target_temp = prompt_float("Enter the target/peak temperature (C): ")
        rise_rate = prompt_float("Enter temperature ramp rate (C per hour): ")
        hold_time = prompt_float("Enter time to hold at target temperature (min): ")
        fall_rate = prompt_float("Enter temperature fall rate (C per hour): ")
        end_temp = prompt_float("Enter the ending temperature (C): ")

        # Set initial temperature and wait
        set_constant_temperature(bath, init_temp)
        wait_for_temperature(bath, init_temp)
        print("Initial temperature reached.")
        input("Press Enter/Return to start temperature ramp...")

        # Ramp up
        ramp_temperature(bath, init_temp, target_temp, rise_rate)
        print("Peak temperature reached.")
        hold_temperature(bath, hold_time)

        # Ramp down
        ramp_temperature(bath, target_temp, end_temp, fall_rate)
        print("Ending temperature reached.")
    elif prog == "2":
        bath.write(b"RS\r")
        response = bath.readline()
        try:
            init_temp = float(response[:5])
        except Exception:
            print("Could not parse initial setpoint. Exiting.")
            bath.close()
            sys.exit(1)
        target_temp = prompt_float("Enter the target/peak temperature (C): ")
        rise_rate = prompt_float("Enter temperature ramp rate (C per hour): ")
        ramp_temperature(bath, init_temp, target_temp, rise_rate)
        print("Peak temperature reached.")
    elif prog == "3":
        print("\n--- Stepwise Temperature Profile Setup ---")
        cloud_point = prompt_float("Enter oil cloud point (C): ")
        step_size = prompt_float("Enter step size for temperature drops (C, e.g., 5): ")
        hold_time = prompt_float("Enter hold time at each step (minutes, e.g., 60): ")
        min_temp = prompt_float("Enter minimum temperature to reach (C): ")
        initial_overheat = prompt_float("Enter degrees above cloud point for initial heat (default 10): ")
        stepwise_profile(
            bath,
            cloud_point=cloud_point,
            step_size=step_size,
            hold_time=hold_time,
            min_temp=min_temp,
            initial_overheat=initial_overheat
        )
    else:
        print("Invalid selection.")

    try:
        bath.close()
        print("Closed serial connection.")
    except Exception:
        print("Serial connection failed to close.")

if __name__ == "__main__":
    main()