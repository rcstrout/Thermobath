"""
Hardware interfaces for data acquisition devices.
This module provides PressureSource implementations for various DAQ hardware.
"""

import time
from PyQt5.QtCore import QThread, pyqtSignal
try:
    import serial
except ImportError:
    serial = None

from .core import PressureSource


class DataqSerialPressureSource(PressureSource):
    """Pressure source that communicates with a DATAQ USB device over serial."""

    def __init__(self, com_port, baudrate=9600, timeout_s=0.5, write_timeout_s=0.5):
        if serial is None:
            raise ImportError("pyserial is not installed. Install it with 'pip install pyserial'.")

        self.com_port = com_port
        self.serial = serial.Serial(
            port=com_port,
            baudrate=baudrate,
            timeout=timeout_s,
            write_timeout=write_timeout_s,
        )
        self.serial.reset_input_buffer()
        self.serial.reset_output_buffer()

    def _build_read_command(self, channels):
        # Generic ASCII fallback command for simple query/response device modes.
        channel_list = ",".join(str(ch) for ch in channels)
        return f"READ {channel_list}\n".encode("ascii")

    def _parse_ascii_values(self, response_text, channels):
        values = [None] * len(channels)
        if not response_text:
            return values

        # Supports either "v0,v1,..." or "ch:val,ch:val" styles.
        tokens = [token.strip() for token in response_text.replace(";", ",").split(",") if token.strip()]
        if not tokens:
            return values

        channel_index = {ch: idx for idx, ch in enumerate(channels)}
        sequential_idx = 0
        for token in tokens:
            if ":" in token:
                ch_text, val_text = token.split(":", 1)
                try:
                    ch = int(ch_text.strip())
                    if ch in channel_index:
                        values[channel_index[ch]] = float(val_text.strip())
                except (TypeError, ValueError):
                    continue
            else:
                if sequential_idx >= len(values):
                    break
                try:
                    values[sequential_idx] = float(token)
                except ValueError:
                    values[sequential_idx] = None
                sequential_idx += 1

        return values

    def read_channels(self, channels):
        if not channels:
            return []

        if not getattr(self, "serial", None) or not self.serial.is_open:
            return [None] * len(channels)

        try:
            command = self._build_read_command(channels)
            self.serial.reset_input_buffer()
            self.serial.write(command)
            self.serial.flush()

            raw_response = self.serial.readline()
            if not raw_response:
                return [None] * len(channels)

            response_text = raw_response.decode("ascii", errors="ignore").strip()
            return self._parse_ascii_values(response_text, channels)
        except (TimeoutError, serial.SerialTimeoutException, serial.SerialException):
            return [None] * len(channels)
        except Exception:
            return [None] * len(channels)

    def close(self):
        if getattr(self, "serial", None) and self.serial.is_open:
            try:
                self.serial.close()
            except serial.SerialException:
                pass


class DAQMonitorThread(QThread):
    """Poll a DAQ device and emit the latest channel values."""

    data_updated = pyqtSignal(list)
    table_updated = pyqtSignal(list, list)  # channel_names, values
    status_updated = pyqtSignal(str)

    def __init__(self, reader, channels, channel_names, interval_s=1.0):
        super().__init__()
        self.reader = reader
        self.channels = channels
        self.channel_names = channel_names
        self.interval_s = interval_s
        self._running = True

    def run(self):
        while self._running:
            try:
                values = self.reader.read_channels(self.channels)
                self.data_updated.emit(values)
                self.table_updated.emit(self.channel_names, values)
            except Exception as e:
                self.status_updated.emit(f"DAQ Error: {e}")
                # Stop the thread on error to avoid flooding logs
                self._running = False

            self.msleep(int(self.interval_s * 1000))

    def stop(self):
        self._running = False
        if hasattr(self.reader, 'close'):
            self.reader.close()
