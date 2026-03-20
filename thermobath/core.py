"""
Core logic for Cole Parmer Thermobath Controller.
All routines and bath control logic are here.
Import this module in your UI (PyQt, Tkinter, etc.) for separation of concerns.
"""

import time
import os
import csv
import re
import threading
from collections import deque
from pathlib import Path
import sys
try:
    import serial
    from serial.tools import list_ports
    SERIAL_INSTALLED = True
except ImportError:
    SERIAL_INSTALLED = False


def get_available_ports():
    """Return a list of available serial port names."""
    if not SERIAL_INSTALLED:
        return []
    return [port.device for port in list_ports.comports()]


_FLOAT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def f_to_c(temp_f):
    """Convert Fahrenheit to Celsius."""
    return (temp_f - 32.0) * 5.0 / 9.0


def c_to_f(temp_c):
    """Convert Celsius to Fahrenheit."""
    return (temp_c * 9.0 / 5.0) + 32.0


def _extract_first_float(response):
    """Extract first float-like token from serial response bytes/string."""
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="ignore")
    else:
        text = str(response)
    match = _FLOAT_RE.search(text)
    if not match:
        raise ValueError(f"No numeric value found in response: {text!r}")
    return float(match.group(0))


def _response_to_celsius(response, default_unit="C"):
    """Parse a temperature response and normalize to Celsius.

    The chiller may respond like `100.00F` or `37.78C`. If no explicit unit
    exists in the text, `default_unit` is used.
    """
    if isinstance(response, bytes):
        text = response.decode("utf-8", errors="ignore").strip().upper()
    else:
        text = str(response).strip().upper()

    value = _extract_first_float(text)

    # Prefer explicit unit marker when present in the same response line.
    if "F" in text and "C" not in text:
        return f_to_c(value)
    if "C" in text and "F" not in text:
        return value

    return f_to_c(value) if default_unit.upper() == "F" else value



class PressureSource:
    """Interface for pressure acquisition sources.

    Implementations should provide `read_channel` for single-channel reads.
    `get_channel` remains available for compatibility with older call sites.
    """

    def read_channel(self, channel):
        raise NotImplementedError

    def read_channels(self, channels):
        return [self.read_channel(ch) for ch in channels]

    def get_channel(self, channel):
        return self.read_channel(channel)


class LegacyPressureReaderAdapter(PressureSource):
    """Wrap legacy readers exposing only `get_channel`."""

    def __init__(self, reader):
        self.reader = reader

    def read_channel(self, channel):
        return self.reader.get_channel(channel)


class ChannelEngineeringPressureSource(PressureSource):
    """Apply per-channel linear scaling from voltage to engineering units.

    Channel keys are matched by their string form (e.g. "0", "P1").
    Each config item is a dict with:
      - volt_low, volt_high
      - eng_low, eng_high
    """

    def __init__(self, source, channel_map=None):
        self.source = ensure_pressure_source(source)
        self.channel_map = channel_map or {}

    @staticmethod
    def _scale_value(value, cfg):
        if value is None:
            return None
        try:
            raw = float(value)
            v_low = float(cfg.get("volt_low", 0.0))
            v_high = float(cfg.get("volt_high", 5.0))
            e_low = float(cfg.get("eng_low", 0.0))
            e_high = float(cfg.get("eng_high", 100.0))
        except Exception:
            return None

        if abs(v_high - v_low) < 1e-12:
            return e_low

        ratio = (raw - v_low) / (v_high - v_low)
        return e_low + ratio * (e_high - e_low)

    def read_channel(self, channel):
        raw = self.source.read_channel(channel)
        cfg = self.channel_map.get(str(channel))
        if not cfg:
            return raw
        return self._scale_value(raw, cfg)


class PressureRateMonitor:
    """Track pressure history and compute dP/dt per channel over a time window."""

    def __init__(self, window_size):
        self.window_size = max(0.0, float(window_size))
        self._channel_windows = []

    def _trim_old(self, now_ts):
        cutoff = now_ts - self.window_size
        for window in self._channel_windows:
            while window and window[0][0] < cutoff:
                window.popleft()

    def update(self, pressures):
        """Add a new timestamped pressure sample list and trim stale samples."""
        now_ts = time.time()
        pressure_values = list(pressures) if pressures is not None else []

        while len(self._channel_windows) < len(pressure_values):
            self._channel_windows.append(deque())

        for idx, value in enumerate(pressure_values):
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            self._channel_windows[idx].append((now_ts, numeric_value))

        self._trim_old(now_ts)

    @staticmethod
    def _slope(samples):
        if len(samples) < 2:
            return 0.0

        n = float(len(samples))
        sum_t = 0.0
        sum_p = 0.0
        sum_tt = 0.0
        sum_tp = 0.0

        for ts, pressure in samples:
            sum_t += ts
            sum_p += pressure
            sum_tt += ts * ts
            sum_tp += ts * pressure

        denom = (n * sum_tt) - (sum_t * sum_t)
        if abs(denom) < 1e-12:
            return 0.0

        return ((n * sum_tp) - (sum_t * sum_p)) / denom

    def get_rates(self):
        """Return pressure rate (units/second) for each tracked channel."""
        self._trim_old(time.time())
        return [self._slope(window) for window in self._channel_windows]


class CsvDataLogger:
    """Write timestamped rows to a CSV file.

    Thread-safe: write_row may be called from any thread.
    Appends to an existing file; writes a header row only when the file is new
    or empty.
    """

    def __init__(self, file_path, headers):
        self.path = Path(file_path)
        self.headers = list(headers)
        self._lock = threading.Lock()
        self._row_count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        self._file = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        if write_header:
            self._writer.writerow(self.headers)
            self._file.flush()

    def write_row(self, values):
        with self._lock:
            self._writer.writerow(values)
            self._file.flush()
            self._row_count += 1

    @property
    def row_count(self):
        return self._row_count

    def close(self):
        with self._lock:
            try:
                self._file.close()
            except Exception:
                pass


def ensure_pressure_source(pressure_reader):
    """Normalize a pressure input into a PressureSource instance."""
    if pressure_reader is None:
        return None
    if isinstance(pressure_reader, PressureSource):
        return pressure_reader
    if hasattr(pressure_reader, "get_channel"):
        return LegacyPressureReaderAdapter(pressure_reader)
    raise TypeError("pressure_reader must implement PressureSource or get_channel(channel)")


class BathInterface:
    """Abstract base class for bath communication."""
    def write(self, cmd):
        raise NotImplementedError

    def readline(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class SerialInterface(BathInterface):
    """Real serial communication with a hardware device."""
    def __init__(self, port, baudrate=19200, timeout=1):
        if not SERIAL_INSTALLED:
            raise ImportError("pyserial is not installed. Cannot create SerialInterface.")
        self.serial = serial.Serial(
            port,
            baudrate=baudrate,
            bytesize=8,
            stopbits=1,
            parity=serial.PARITY_NONE,
            timeout=timeout,
        )
        # Clear any stale data in the input buffer
        self.serial.reset_input_buffer()
        time.sleep(0.1)
        
        # Mirror legacy startup behavior: enable echo and consume response.
        self.serial.write(b"SE1\r")
        self.serial.flush()
        time.sleep(0.1)
        self.serial.readline()

    def write(self, cmd):
        try:
            log_debug(f"USB TX -> {cmd!r}")
        except Exception:
            pass
        self.serial.write(cmd)
        self.serial.flush()  # Ensure data is sent immediately

    def readline(self):
        resp = self.serial.readline()
        try:
            log_debug(f"USB RX <- {resp!r}")
        except Exception:
            pass
        return resp

    def close(self):
        self.serial.close()


def log_debug(msg):
    """Log debug message to both console and a log file."""
    print(msg, file=sys.stderr)
    try:
        from pathlib import Path
        log_dir = Path.home() / "AppData" / "Local" / "ThermobathController"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "debug.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def set_constant_temperature(bath, set_temp):
    """Set and hold a constant temperature.
    
    Args:
        bath: Serial interface to chiller
        set_temp: Target temperature in Celsius
    
    Returns:
        Empty string on success, error message on failure
    """
    log_debug(f"\n=== Setting constant temperature to {set_temp:.2f}°C ({c_to_f(set_temp):.2f}°F) ===")
    
    # Enable output mode
    log_debug(f"Sending: SO 1\\r")
    bath.write(b"SO 1\r")
    time.sleep(0.2)
    resp = bath.readline()
    log_debug(f"SO response: {resp!r}")
    
    # Wait a bit before setting
    time.sleep(0.3)
    
    # Set the setpoint in Celsius (device always expects Celsius for SS command)
    command = f"SS {set_temp:.2f}\r".encode('utf-8')
    log_debug(f"Sending: {command!r} (Celsius)")
    bath.write(command)
    time.sleep(0.2)
    resp = bath.readline()
    log_debug(f"SS response: {resp!r}")
    
    # Wait longer for the bath to process the setpoint
    time.sleep(2)
    
    # Read setpoint back multiple times for robustness
    # Convert response from Fahrenheit back to Celsius for comparison
    confirmed = False
    last_error = ""
    for attempt in range(3):
        log_debug(f"RS attempt {attempt+1}/3...")
        bath.write(b"RS\r")
        time.sleep(0.2)
        response = bath.readline()
        log_debug(f"RS attempt {attempt+1} response: {response!r}")
        
        if not response:
            last_error = f"Attempt {attempt+1}: No response from chiller"
            log_debug(last_error)
            continue
        
        try:
            response_temp_c = _response_to_celsius(response, default_unit="F")
            response_value = _extract_first_float(response)
            log_debug(f"Extracted: {response_value:.2f} raw ({response_temp_c:.2f}°C), Target: {set_temp:.2f}°C ({c_to_f(set_temp):.2f}°F), Diff: {abs(response_temp_c - set_temp):.2f}°C")
            
            # Check if setpoint was actually set (within 0.5 degrees Celsius for tolerance)
            if abs(response_temp_c - set_temp) < 0.5:
                confirmed = True
                log_debug(f"✓ Setpoint CONFIRMED: {response_temp_c:.2f}°C ({response_value:.2f}°F)")
                break
            else:
                last_error = f"Setpoint mismatch: set {set_temp:.2f}°C, got {response_temp_c:.2f}°C"
                log_debug(last_error)
        except Exception as e:
            last_error = f"Parse error: {e}"
            log_debug(last_error)
            continue
    
    if not confirmed:
        msg = f"Setpoint confirmation failed after {attempt+1} attempts. Last error: {last_error}"
        log_debug(f"✗ {msg}")
        return msg
    
    return ""

def wait_for_temperature(
    bath,
    target_temp,
    tolerance=0.1,
    poll_interval=1,
    callback=None,
    max_wait_seconds=3600,
):
    """Wait until bath reaches target temperature within tolerance."""
    start = time.time()
    while True:
        if max_wait_seconds is not None and (time.time() - start) > max_wait_seconds:
            raise TimeoutError(f"Timed out waiting for {target_temp:.2f} C")
        time.sleep(poll_interval)
        bath.write(b"RT\r")
        response = bath.readline()
        try:
            current_temp = _response_to_celsius(response, default_unit="F")
        except Exception:
            continue

        # Keep stop/pause callback exceptions visible to caller.
        if callback:
            callback(current_temp)

        if abs(current_temp - target_temp) < tolerance:
            break

def ramp_temperature(bath, start_temp, target_temp, rate_c_per_hr, callback=None):
    """Ramp temperature from start_temp to target_temp at given rate."""
    temp_diff = target_temp - start_temp
    ramp_duration_hr = abs(temp_diff) / rate_c_per_hr if rate_c_per_hr != 0 else 0
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
        if callback:
            callback(current_set)
        time.sleep(60)  # Wait one minute per setpoint step
        current_set += rate_c_per_min
        if (rate_c_per_min > 0 and current_set > target_temp) or (rate_c_per_min < 0 and current_set < target_temp):
            current_set = target_temp
    command = f"SS {target_temp:.2f}\r".encode('utf-8')
    bath.write(command)
    bath.readline()
    wait_for_temperature(bath, target_temp, tolerance=0.05, poll_interval=1, callback=callback)

def hold_temperature(
    bath,
    hold_time_min,
    callback=None,
    pressure_reader=None,
    pressure_channel=None,
    pressure_max=None,
    check_interval=5,
    over_pressure_behavior="Extend Hold",
):
    """Hold current temperature, optionally monitoring pressure.

    If `pressure_reader` and `pressure_channel` are provided, the loop will
    extend the hold while the pressure exceeds `pressure_max`.

    `pressure_channel` may be an int/string (single channel) or a list/tuple
    to monitor multiple channels (e.g., 4 loops).
    """
    pressure_source = ensure_pressure_source(pressure_reader)
    start_time = time.time()
    end_time = start_time + hold_time_min * 60

    while time.time() < end_time:
        if callback:
            callback()

        if pressure_source is not None and pressure_channel is not None and pressure_max is not None:
            channels = (
                [pressure_channel]
                if not isinstance(pressure_channel, (list, tuple))
                else list(pressure_channel)
            )
            pressures = []
            for ch in channels:
                try:
                    val = pressure_source.read_channel(ch)
                except Exception:
                    val = None
                pressures.append(val)

            # Inform caller about the latest pressures
            if callback:
                callback({'pressure': pressures})

            # If any reading is above max, handle based on behavior
            if any(p is not None and p > pressure_max for p in pressures):
                if over_pressure_behavior == "Extend Hold":
                    end_time = max(end_time, time.time()) + check_interval
                elif over_pressure_behavior == "Pause":
                    if callback:
                        callback("pause_on_pressure")
                    # Wait for resume or stop
                    while True:
                        time.sleep(1)
                        if callback and callback() == "resume":
                            break
                elif over_pressure_behavior == "Abort":
                    if callback:
                        callback("abort_on_pressure")
                    raise Exception("Aborted due to over-pressure")

        time.sleep(check_interval)

def stepwise_profile(
    bath,
    cloud_point,
    step_size,
    hold_time,
    min_temp,
    initial_overheat=10,
    callback=None,
    pressure_reader=None,
    pressure_channel=None,
    pressure_max=None,
    pressure_check_interval=5,
    over_pressure_behavior="Extend Hold",
):
    """Run a stepwise temperature profile.

    If `pressure_reader` and `pressure_channel` are provided, the hold step will
    extend while pressure exceeds `pressure_max`.
    """
    overheat_temp = cloud_point + initial_overheat
    set_constant_temperature(bath, overheat_temp)
    wait_for_temperature(bath, overheat_temp, callback=callback)
    if callback:
        callback("dose")  # Signal UI to prompt for dosing
    ramp_temperature(
        bath,
        overheat_temp,
        cloud_point,
        rate_c_per_hr=5,
        callback=callback,
    )
    current_temp = cloud_point
    while current_temp > min_temp:
        next_temp = max(current_temp - step_size, min_temp)
        ramp_temperature(
            bath,
            current_temp,
            next_temp,
            rate_c_per_hr=5,
            callback=callback,
        )
        hold_temperature(
            bath,
            hold_time,
            callback=callback,
            pressure_reader=pressure_reader,
            pressure_channel=pressure_channel,
            pressure_max=pressure_max,
            check_interval=pressure_check_interval,
            over_pressure_behavior=over_pressure_behavior,
        )
        current_temp = next_temp


def smart_dynamic_profile(
    bath,
    cloud_point,
    min_temp,
    initial_overheat=10,
    callback=None,
    pressure_reader=None,
    pressure_channel=None,
    dp_dt_threshold=0.01,
    monitor_window=30.0,
    cooling_step=0.1,
    cooling_interval_s=30.0,
    pressure_check_interval=1.0,
):
    """Run a smart dynamic profile with continuous cooling and dP/dt monitoring.

    Flow:
      1. Overheat and stabilize.
      2. Request dose confirmation via callback("dose").
      3. Ramp down to cloud point.
      4. Continuously cool toward min_temp in small setpoint steps.
      5. During cooling, compute per-channel pressure slope (dP/dt) over a
         sliding window. If any channel exceeds `dp_dt_threshold`, pause cooling
         and hold at the current setpoint until callback indicates resume.
    """

    pressure_source = ensure_pressure_source(pressure_reader)
    if pressure_channel is None:
        channels = []
    elif isinstance(pressure_channel, (list, tuple)):
        channels = list(pressure_channel)
    else:
        channels = [pressure_channel]

    rate_monitor = PressureRateMonitor(window_size=monitor_window)
    poll_s = max(0.2, float(pressure_check_interval))
    cooling_step = abs(float(cooling_step))
    cooling_interval_s = max(poll_s, float(cooling_interval_s))
    threshold = float(dp_dt_threshold)

    def _send_setpoint(temp_c):
        cmd = f"SS {temp_c:.2f}\r".encode("utf-8")
        bath.write(cmd)
        bath.readline()

    def _read_current_temp():
        bath.write(b"RT\r")
        response = bath.readline()
        return _response_to_celsius(response, default_unit="F")

    def _read_pressures():
        if pressure_source is None or not channels:
            return []
        try:
            pressures = pressure_source.read_channels(channels)
        except Exception:
            pressures = []
            for ch in channels:
                try:
                    pressures.append(pressure_source.read_channel(ch))
                except Exception:
                    pressures.append(None)
        if len(pressures) < len(channels):
            pressures = list(pressures) + [None] * (len(channels) - len(pressures))
        return list(pressures[: len(channels)])

    def _emit_live_updates(target_temp):
        try:
            current_temp = _read_current_temp()
            if callback:
                callback(current_temp)
        except Exception:
            pass

        if pressure_source is None or not channels:
            return [], []

        pressures = _read_pressures()
        rate_monitor.update(pressures)
        rates = rate_monitor.get_rates()
        if callback:
            callback({"target": target_temp, "pressure": pressures, "rates": rates})
        return pressures, rates

    overheat_temp = cloud_point + initial_overheat
    set_constant_temperature(bath, overheat_temp)
    wait_for_temperature(bath, overheat_temp, callback=callback)

    if callback:
        callback("dose")

    ramp_temperature(
        bath,
        overheat_temp,
        cloud_point,
        rate_c_per_hr=5,
        callback=callback,
    )

    current_target = float(cloud_point)
    if callback:
        callback({"target": current_target})

    while current_target > min_temp:
        next_target = max(current_target - cooling_step, float(min_temp))
        _send_setpoint(next_target)
        current_target = next_target
        if callback:
            callback({"target": current_target})

        end_slice = time.time() + cooling_interval_s
        while time.time() < end_slice:
            _, rates = _emit_live_updates(current_target)
            if rates and any(rate > threshold for rate in rates):
                # Freeze at the current setpoint until the caller resumes.
                _send_setpoint(current_target)
                pause_result = None
                if callback:
                    pause_result = callback("pause_on_pressure")
                    callback({"target": current_target, "plateau": True, "rates": rates})

                # If callback requests explicit hold-loop control, remain in hold
                # until callback returns "resume". Otherwise assume callback
                # already handled pause/resume (existing UI behavior).
                if pause_result == "hold":
                    while True:
                        _emit_live_updates(current_target)
                        resume = callback("holding_on_dpdt") if callback else None
                        if resume == "resume":
                            break
                        time.sleep(poll_s)

            time.sleep(poll_s)

# Add more routines as needed for future expansion (e.g., pressure-based stop)