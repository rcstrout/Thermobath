"""
Hardware interfaces for data acquisition devices.
This module provides PressureSource implementations for various DAQ hardware.
"""

import time
from PyQt5.QtCore import QThread, pyqtSignal
try:
    from daqhats import mcc128, OptionFlags, HatIDs, HatError, hat_list
    DAQ_HATS_INSTALLED = True
except ImportError:
    DAQ_HATS_INSTALLED = False

from .core import PressureSource

def discover_mcc128_address():
    """Discover the address of the first available MCC 128 HAT."""
    if not DAQ_HATS_INSTALLED:
        return None
    
    hats = hat_list(filter_by_id=HatIDs.MCC_128)
    if not hats:
        return None
    
    return hats[0].address



class MCC128PressureSource(PressureSource):
    """Pressure source backed by a Measurement Computing MCC128 DAQ HAT.

    Args:
        address (int): The board address of the DAQ HAT.
    """

    def __init__(self, address=0):
        if not DAQ_HATS_INSTALLED:
            raise ImportError("daqhats library is not installed. Cannot use MCC128.")

        self.address = address
        self.hat = mcc128(self.address)

        # In case the HAT was left in a different state
        # Set the channel mask to read all channels initially
        self.hat.a_in_mode_write(0) # Single-ended mode
        self.hat.a_in_range_write(0) # +/- 10V range, can be configured if needed

    def read_channel(self, channel):
        """Read a single voltage value from the specified channel."""
        try:
            return self.hat.a_in_read(channel)
        except HatError as e:
            # Handle DAQ errors, e.g., device not found
            print(f"Error reading from MCC128 channel {channel}: {e}")
            return None

    def read_channels(self, channels):
        """Read voltage values from a list of channels."""
        # The MCC128 library doesn't have a bulk read for arbitrary channels,
        # so we read them one by one.
        return [self.read_channel(ch) for ch in channels]

    def close(self):
        """No explicit close needed for mcc128, but included for consistency."""
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
