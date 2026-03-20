"""
PyQt5 UI with dark theme, icons, and a circular thermostat gauge.
"""

import sys
import os
import math
import json
from pathlib import Path
import time
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QFileDialog,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
    QGridLayout,
    QSizePolicy,
    QDialog,
    QMessageBox,
)

from PyQt5.QtGui import QIcon, QPainter, QPen, QColor, QFont
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRectF, QMutex, QWaitCondition, QTimer, QPointF
from . import core as thermobath_core
from . import daq


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    else:
        # For development, the base path is the project root
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    return os.path.join(base_path, relative_path)


def user_config_dir():
    """Return a writable config directory for the current user."""
    base_dir = os.environ.get("LOCALAPPDATA")
    if not base_dir:
        base_dir = str(Path.home() / "AppData" / "Local")
    return Path(base_dir) / "ThermobathController"


def f_to_c(temp_f):
    return (temp_f - 32.0) * 5.0 / 9.0


def c_to_f(temp_c):
    return (temp_c * 9.0 / 5.0) + 32.0


# --- Circular Thermostat Widget ---
class ThermostatGauge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_temp = 77.0
        self.target_temp = 77.0
        self.unit = "F"
        self.min_display_temp = -58.0
        self.max_display_temp = 302.0
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_unit(self, unit):
        self.unit = unit
        if unit == "F":
            self.min_display_temp = -58.0
            self.max_display_temp = 302.0
        else:
            self.min_display_temp = -50.0
            self.max_display_temp = 150.0
        self.update()

    def set_temps(self, current, target):
        self.current_temp = current
        self.target_temp = target
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate dynamic sizes based on widget dimensions
        size = min(self.width(), self.height())
        margin = size * 0.1  # 10% margin
        rect = QRectF(margin, margin, size - 2*margin, size - 2*margin)

        # Draw background
        painter.setBrush(QColor(30, 30, 30))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(rect)

        # Draw arc for target
        pen_width = max(6, size // 40)  # Scale pen width with size
        pen = QPen(QColor(100, 100, 255), pen_width)
        painter.setPen(pen)
        target_ratio = (self.target_temp - self.min_display_temp) / (self.max_display_temp - self.min_display_temp)
        target_ratio = max(0.0, min(1.0, target_ratio))
        span_angle = int(270 * 16 * target_ratio)
        painter.drawArc(rect, 135 * 16, span_angle)

        # Draw arc for current
        pen = QPen(QColor(0, 220, 120), pen_width)
        painter.setPen(pen)
        current_ratio = (self.current_temp - self.min_display_temp) / (self.max_display_temp - self.min_display_temp)
        current_ratio = max(0.0, min(1.0, current_ratio))
        span_angle = int(270 * 16 * current_ratio)
        painter.drawArc(rect, 135 * 16, span_angle)

        # Draw current temperature in center
        font_size = max(12, size // 12)  # Scale font with size
        painter.setPen(QColor(220, 220, 220))
        font = QFont("Arial", font_size, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, f"{self.current_temp:.1f}°{self.unit}")

        # Draw target temperature below the center
        small_font_size = max(8, size // 20)
        painter.setFont(QFont("Arial", small_font_size))
        target_y_offset = size * 0.25  # Position relative to size
        target_rect = QRectF(rect.left(), rect.center().y() + target_y_offset,
                           rect.width(), target_y_offset)
        painter.drawText(target_rect, Qt.AlignHCenter | Qt.AlignTop,
                        f"Target: {self.target_temp:.1f}°{self.unit}")


class PressureDataDialog(QDialog):
    """Dialog to display real-time pressure data in a table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pressure Monitor Data")
        self.setModal(False)
        self.resize(300, 200)

        layout = QVBoxLayout(self)

        # Real-time pressure table
        self.pressure_table = QTableWidget(4, 2)
        self.pressure_table.setHorizontalHeaderLabels(["Channel", "Value"])
        self.pressure_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.pressure_table.setMaximumHeight(150)

        for row in range(4):
            self.pressure_table.setItem(row, 0, QTableWidgetItem(f"Ch {row+1}"))
            self.pressure_table.setItem(row, 1, QTableWidgetItem("--"))

        layout.addWidget(self.pressure_table)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def update_pressure_data(self, channel_names, values):
        """Update the table with new pressure data."""
        for i, (name, value) in enumerate(zip(channel_names, values)):
            if i < self.pressure_table.rowCount():
                self.pressure_table.setItem(i, 0, QTableWidgetItem(name))
                self.pressure_table.setItem(i, 1, QTableWidgetItem(f"{value:.3f}" if value is not None else "--"))


class CommandReferenceDialog(QDialog):
    """Display how USB serial commands are framed and sent."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("USB Command Reference")
        self.resize(520, 280)

        layout = QVBoxLayout(self)
        text = QLabel(
            "Transport: USB serial COM port via pyserial\n"
            "All commands are ASCII strings terminated with carriage return (\\r).\n\n"
            "Common commands:\n"
            "  SE1\\r  - Enable echo\n"
            "  SO 1\\r - Output on\n"
            "  SO 0\\r - Standby/output off\n"
            "  SS <tempC>\\r - Set setpoint in Celsius\n"
            "  RT\\r  - Read current temperature\n"
            "  RS\\r  - Read setpoint\n\n"
            "Example write path:\n"
            "  SerialInterface.write(cmd_bytes) -> serial.write(...) -> serial.flush()"
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)


class EngineeringSettingsDialog(QDialog):
    """Per-channel scaling from voltage to engineering units."""

    HEADERS = ["Channel", "Volt Low", "Volt High", "Eng Low", "Eng High", "EU Label"]

    def __init__(self, channel_configs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Engineering Settings")
        self.resize(760, 260)
        self._channel_configs = channel_configs

        layout = QVBoxLayout(self)
        self.table = QTableWidget(4, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        for i in range(4):
            cfg = channel_configs[i] if i < len(channel_configs) else {}
            self.table.setItem(i, 0, QTableWidgetItem(cfg.get("label", f"Ch {i+1}")))
            self.table.setItem(i, 1, QTableWidgetItem(str(cfg.get("volt_low", 0.0))))
            self.table.setItem(i, 2, QTableWidgetItem(str(cfg.get("volt_high", 5.0))))
            self.table.setItem(i, 3, QTableWidgetItem(str(cfg.get("eng_low", 0.0))))
            self.table.setItem(i, 4, QTableWidgetItem(str(cfg.get("eng_high", 100.0))))
            self.table.setItem(i, 5, QTableWidgetItem(cfg.get("eu_label", "EU")))

        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        apply_button = QPushButton("Apply")
        close_button = QPushButton("Cancel")
        apply_button.clicked.connect(self.accept)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(apply_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _parse_float(self, row, col, default):
        item = self.table.item(row, col)
        if item is None:
            return default
        try:
            return float(item.text().strip())
        except Exception:
            return default

    def get_settings(self):
        settings = []
        for i in range(4):
            label_item = self.table.item(i, 0)
            eu_item = self.table.item(i, 5)
            settings.append(
                {
                    "label": (label_item.text().strip() if label_item else f"Ch {i+1}"),
                    "volt_low": self._parse_float(i, 1, 0.0),
                    "volt_high": self._parse_float(i, 2, 5.0),
                    "eng_low": self._parse_float(i, 3, 0.0),
                    "eng_high": self._parse_float(i, 4, 100.0),
                    "eu_label": (eu_item.text().strip() if eu_item else "EU"),
                }
            )
        return settings


class DosePromptDialog(QDialog):
    """Dialog to prompt user to dose products with a countdown timer."""

    def __init__(self, parent=None, timeout_seconds=900):  # 15 minutes = 900 seconds
        super().__init__(parent)
        self.setWindowTitle("Dose Products")
        self.setModal(True)
        self.resize(400, 200)
        
        self.timeout_seconds = timeout_seconds
        self.remaining_seconds = timeout_seconds
        self.confirmed = False
        
        layout = QVBoxLayout(self)
        
        # Title label
        title_label = QLabel("Ready to dose products")
        title_font = QFont("Arial", 14, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #ffffff;")
        layout.addWidget(title_label)
        
        # Instructions
        instructions_label = QLabel("Inject the sample, then click OK to continue.\nAutomatic pause in:")
        instructions_label.setStyleSheet("color: #cccccc; margin-top: 10px;")
        layout.addWidget(instructions_label)
        
        # Countdown timer label
        self.countdown_label = QLabel(self._format_time(self.remaining_seconds))
        countdown_font = QFont("Arial", 32, QFont.Bold)
        self.countdown_label.setFont(countdown_font)
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setStyleSheet("color: #00dc78; margin: 20px 0px;")
        layout.addWidget(self.countdown_label)
        
        # OK button
        ok_button = QPushButton("OK - Continue")
        ok_button.clicked.connect(self.confirm_and_close)
        ok_button.setMinimumHeight(40)
        ok_button.setStyleSheet("""
            QPushButton {
                background-color: #00dc78;
                color: #000000;
                border: none;
                border-radius: 5px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #00ff8a;
            }
        """)
        layout.addWidget(ok_button)
        
        # Timer to update countdown
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_countdown)
        self.timer.start(1000)  # Update every 1 second
        
        # Style the dialog with dark theme
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                border: 1px solid #3d3d3d;
            }
        """)

    def _format_time(self, seconds):
        """Format seconds as MM:SS"""
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"

    def update_countdown(self):
        """Update the countdown and close if timeout reached."""
        self.remaining_seconds -= 1
        self.countdown_label.setText(self._format_time(max(0, self.remaining_seconds)))
        
        # Color change when time is running out (last 60 seconds)
        if self.remaining_seconds <= 60:
            self.countdown_label.setStyleSheet("color: #ff6b6b; margin: 20px 0px;")
        
        if self.remaining_seconds <= 0:
            self.timer.stop()
            self.confirmed = False  # Timeout = not confirmed
            self.accept()  # Close dialog

    def confirm_and_close(self):
        """User clicked OK."""
        self.timer.stop()
        self.confirmed = True
        self.accept()
    
    def is_confirmed(self):
        """Return True if user clicked OK before timeout."""
        return self.confirmed


class PressurePlot(QWidget):
    """Simple scrolling plot widget for multiple channels."""

    def __init__(self, channels=4, history_len=200, parent=None):
        super().__init__(parent)
        self.channels = channels
        self.history_len = history_len
        self.data = [[None] * history_len for _ in range(channels)]
        self.colors = [
            QColor(0, 220, 120),
            QColor(100, 100, 255),
            QColor(255, 130, 0),
            QColor(220, 60, 60),
        ]
        self.autoscale = True
        self.fixed_min = 0.0
        self.fixed_max = 1.0
        self.setMinimumHeight(150)
        self.setMaximumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_scale(self, autoscale=True, y_min=0.0, y_max=1.0):
        """Configure plotting range."""
        self.autoscale = autoscale
        self.fixed_min = y_min
        self.fixed_max = y_max
        self.update()

    def update_values(self, values):
        """Append new channel values and repaint."""
        for idx in range(self.channels):
            v = values[idx] if idx < len(values) else None
            self.data[idx].append(v)
            if len(self.data[idx]) > self.history_len:
                self.data[idx].pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(10, 10, -10, -10)
        painter.fillRect(rect, QColor(30, 30, 30))

        # Determine scaling based on current data or fixed range
        if self.autoscale:
            all_values = [v for channel in self.data for v in channel if v is not None]
            if all_values:
                vmin = min(all_values)
                vmax = max(all_values)
            else:
                vmin, vmax = 0.0, 1.0
        else:
            vmin, vmax = self.fixed_min, self.fixed_max

        # Avoid degenerate range
        if vmax <= vmin:
            vmax = vmin + 1.0

        # Draw grid lines
        painter.setPen(QColor(60, 60, 60))
        for i in range(1, 4):
            y = rect.top() + (i * rect.height()) // 4
            painter.drawLine(rect.left(), y, rect.right(), y)

        # Draw each channel line
        for idx, channel in enumerate(self.data):
            pen = QPen(self.colors[idx % len(self.colors)])
            pen.setWidth(2)
            painter.setPen(pen)
            points = []
            for i, v in enumerate(channel):
                if v is None:
                    continue
                x = rect.left() + (i / max(1, self.history_len - 1)) * rect.width()
                y = rect.bottom() - ((v - vmin) / (vmax - vmin)) * rect.height()
                points.append(QPointF(x, y))

            # Draw lines between points
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)

        # Draw min/max labels
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(rect.left() + 4, rect.top() + 12, f"Max: {vmax:.3f}")
        painter.drawText(rect.left() + 4, rect.bottom() - 4, f"Min: {vmin:.3f}")


class BathWorker(QThread):
    update_status = pyqtSignal(str)
    update_gauge = pyqtSignal(float, float)
    update_temps = pyqtSignal(float, float)  # For separate temperature display widgets
    comm_status = pyqtSignal(str, bool)
    finished = pyqtSignal()

    def __init__(
        self,
        routine,
        params,
        port,
        temp_unit="F",
        pressure_reader=None,
        pressure_channels=None,
        pressure_max=None,
        pressure_check_interval=5,
        over_pressure_behavior="Extend Hold",
        parent_widget=None,
    ):
        super().__init__()
        self.routine = routine
        self.params = params
        self.port = port
        self.temp_unit = temp_unit
        self.pressure_reader = pressure_reader
        self.pressure_channels = pressure_channels
        self.pressure_max = pressure_max
        self.pressure_check_interval = pressure_check_interval
        self.over_pressure_behavior = over_pressure_behavior
        self.parent_widget = parent_widget
        self._bath = None
        self._active_target_c = params.get('set_temp', params.get('cloud_point', 25.0))
        self._standby = False
        self._paused = False
        self._stopped = False
        self._mutex = QMutex()
        self._pause_cond = QWaitCondition()

    def pause(self):
        self._mutex.lock()
        self._paused = True
        if self._bath is not None and not self._standby and not self._stopped:
            try:
                self._bath.write(b"SO 0\r")
                self._bath.readline()
                self._standby = True
                self.update_status.emit("Paused (Standby: output off)")
            except Exception as e:
                self.update_status.emit(f"Pause standby command failed: {e}")
        self._mutex.unlock()

    def resume(self):
        self._mutex.lock()
        if self._bath is not None and self._standby and not self._stopped:
            try:
                self._bath.write(b"SO 1\r")
                self._bath.readline()
                command = f"SS {self._active_target_c:.2f}\r".encode('utf-8')
                self._bath.write(command)
                self._bath.readline()
                self._standby = False
                self.update_status.emit("Resuming from standby...")
            except Exception as e:
                self.update_status.emit(f"Resume command failed: {e}")
        self._paused = False
        self._pause_cond.wakeAll()
        self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        self._stopped = True
        self._paused = False
        self._pause_cond.wakeAll()
        self._mutex.unlock()

    def check_pause_stop(self):
        self._mutex.lock()
        while self._paused and not self._stopped:
            self.update_status.emit("Paused...")
            self._pause_cond.wait(self._mutex)
        stopped = self._stopped
        self._mutex.unlock()
        return stopped

    def check_pressure_pause(self):
        self._mutex.lock()
        paused = self._paused
        self._mutex.unlock()
        return paused

    def _to_display_temp(self, temp_c):
        return c_to_f(temp_c) if self.temp_unit == "F" else temp_c

    def run(self):
        try:
            # Import core logging function
            from . import core as thermobath_core
            bath = thermobath_core.SerialInterface(self.port)
            self.comm_status.emit(f"Connected to {self.port}", True)
            self._bath = bath
        except Exception as e:
            self.comm_status.emit(f"Connection failed: {e}", False)
            self.finished.emit()
            return

        target_c = self.params['set_temp'] if self.routine == "Constant" else self.params.get('cloud_point', 25.0)
        self._active_target_c = target_c
        def callback(status=None):
            if self.check_pause_stop():
                raise Exception("Stopped")
            if isinstance(status, (float, int)):
                status_display = self._to_display_temp(status)
                target_display = self._to_display_temp(target_c)
                self.update_status.emit(f"Current temp: {status_display:.2f} °{self.temp_unit}")
                self.update_gauge.emit(status_display, target_display)
            elif status == "dose":
                # Show dose prompt dialog with countdown timer
                dialog = DosePromptDialog(self.parent_widget, timeout_seconds=900)
                result = dialog.exec_()
                if not dialog.is_confirmed():
                    # Timeout occurred - pause the run
                    self.update_status.emit("Dose timeout - pausing run. Click Resume to continue.")
                    self.pause()
                    while self.check_pressure_pause():
                        time.sleep(0.5)
                else:
                    self.update_status.emit("Dosed. Continuing...")
        try:
            if self.routine == "Constant":
                error_msg = thermobath_core.set_constant_temperature(bath, self.params['set_temp'])
                if error_msg:  # If there's an error message (non-empty string)
                    raise RuntimeError(error_msg)
                self.update_status.emit("Setpoint confirmed. Waiting for bath temperature...")
                thermobath_core.wait_for_temperature(
                    bath,
                    self.params['set_temp'],
                    tolerance=0.5,
                    poll_interval=1,
                    callback=callback,
                    max_wait_seconds=7200,
                )
                self.update_status.emit("Constant temperature reached.")
                temp_display = self._to_display_temp(self.params['set_temp'])
                self.update_gauge.emit(temp_display, temp_display)
            elif self.routine == "Stepwise":
                def stepwise_callback(status=None):
                    if self.check_pause_stop():
                        raise Exception("Stopped")
                    nonlocal target_c
                    if isinstance(status, (float, int)):
                        status_display = self._to_display_temp(status)
                        target_display = self._to_display_temp(target_c)
                        self.update_status.emit(f"Current temp: {status_display:.2f} °{self.temp_unit}")
                        self.update_gauge.emit(status_display, target_display)
                    elif status == "dose":
                        # Show dose prompt dialog with countdown timer
                        dialog = DosePromptDialog(self.parent_widget, timeout_seconds=900)
                        result = dialog.exec_()
                        if not dialog.is_confirmed():
                            # Timeout occurred - pause the run
                            self.update_status.emit("Dose timeout - pausing run. Click Resume to continue.")
                            self.pause()
                            while self.check_pressure_pause():
                                time.sleep(0.5)
                        else:
                            self.update_status.emit("Dosed. Continuing...")
                    elif status == "pause_on_pressure":
                        self.update_status.emit("Paused due to over-pressure. Click Resume to continue.")
                        self.pause()
                        while self.check_pressure_pause():
                            time.sleep(0.5)
                    elif isinstance(status, dict) and 'target' in status:
                        target_c = status['target']
                        self._active_target_c = target_c
                thermobath_core.stepwise_profile(
                    bath,
                    cloud_point=self.params['cloud_point'],
                    step_size=self.params['step_size'],
                    hold_time=self.params['hold_time'],
                    min_temp=self.params['min_temp'],
                    initial_overheat=self.params['initial_overheat'],
                    callback=stepwise_callback,
                    pressure_reader=self.pressure_reader,
                    pressure_channel=self.pressure_channels,
                    pressure_max=self.pressure_max,
                    pressure_check_interval=self.pressure_check_interval,
                    over_pressure_behavior=self.over_pressure_behavior,
                )
                self.update_status.emit("Stepwise profile complete.")
                min_display = self._to_display_temp(self.params['min_temp'])
                self.update_gauge.emit(min_display, min_display)
        except Exception as e:
            if str(e) == "Stopped":
                self.update_status.emit("Stopped by user.")
            else:
                self.update_status.emit(f"Routine failed: {e}")
                self.comm_status.emit(f"Communication error: {e}", False)
        bath.close()
        self._bath = None
        self.finished.emit()

class ThermobathUI(QWidget):
    def __init__(self):
        super().__init__()
        self.engineering_config = self._default_engineering_config()
        self.setWindowTitle("Thermobath Controller")
        self.resize(480, 750)  # Set a reasonable default size
        self.setMinimumSize(450, 700)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(dark_stylesheet())

        # Main container widget and layout
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setSpacing(8)
        self.layout.setContentsMargins(15, 15, 15, 15)
        self.container.setLayout(self.layout)

        # Scroll area
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)

        # Main layout for the window
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.scroll)
        self.setLayout(main_layout)

        # Temperature display at top
        temp_display_layout = QHBoxLayout()
        self.current_temp_label = QLabel("Current: 77.00°F")
        self.current_temp_label.setStyleSheet("color: #8ec07c; font-size: 14px; font-weight: bold;")
        self.target_temp_label = QLabel("Target: 77.00°F")
        self.target_temp_label.setStyleSheet("color: #83a598; font-size: 14px; font-weight: bold;")
        temp_display_layout.addWidget(self.current_temp_label)
        temp_display_layout.addStretch()
        temp_display_layout.addWidget(self.target_temp_label)
        self.layout.addLayout(temp_display_layout)

        # Thermostat gauge
        self.gauge = ThermostatGauge()
        self.layout.addWidget(self.gauge, alignment=Qt.AlignCenter)
        self.layout.addStretch(0)  # Minimal stretch after gauge

        self.routine_box = QComboBox()
        self.routine_box.clear()
        self.routine_box.addItem(QIcon.fromTheme("media-playback-start"), "Constant")
        self.routine_box.addItem(QIcon.fromTheme("view-refresh"), "Stepwise")
        self.layout.addWidget(self.routine_box)

        ribbon_row = QHBoxLayout()
        ribbon_row.addWidget(QLabel("Ribbon:"))
        self.command_help_button = QPushButton("USB Commands")
        self.engineering_button = QPushButton("Engineering Settings")
        ribbon_row.addWidget(self.command_help_button)
        ribbon_row.addWidget(self.engineering_button)
        ribbon_row.addStretch()
        self.layout.addLayout(ribbon_row)

        # Port selection and status
        comm_layout = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_ports_button = QPushButton("Refresh")
        self.test_connection_button = QPushButton("Test Connection")
        self.standby_button = QPushButton("Standby: Off")
        self.comm_status_label = QLabel("Disconnected")
        self.comm_status_label.setStyleSheet("color: #d65d0e;") # Orange

        comm_layout.addWidget(QLabel("Chiller Port:"))
        comm_layout.addWidget(self.port_combo)
        comm_layout.addWidget(self.refresh_ports_button)
        comm_layout.addWidget(self.test_connection_button)
        comm_layout.addWidget(self.standby_button)
        self.temp_unit_combo = QComboBox()
        self.temp_unit_combo.addItems(["F", "C"])
        comm_layout.addWidget(QLabel("Temp Units:"))
        comm_layout.addWidget(self.temp_unit_combo)
        comm_layout.addWidget(self.comm_status_label)
        self.layout.addLayout(comm_layout)

        self.transport_label = QLabel("Transport: USB serial (COM)")
        self.transport_label.setStyleSheet("color: #83a598;")
        self.layout.addWidget(self.transport_label)


        # Constant temp widgets
        self.constant_temp_input = QDoubleSpinBox()
        self.constant_temp_input.setRange(-58, 302)
        self.constant_temp_input.setSuffix(" °F")
        self.constant_temp_input.setValue(77.0)
        self.layout.addLayout(labeled_icon_row("Setpoint Temperature:", "temperature", self.constant_temp_input))

        # Stepwise widgets
        self.cloud_point_input = QDoubleSpinBox()
        self.cloud_point_input.setRange(-58, 302)
        self.cloud_point_input.setSuffix(" °F")
        self.cloud_point_input.setValue(50.0)
        self.step_size_input = QDoubleSpinBox()
        self.step_size_input.setRange(0.1, 90)
        self.step_size_input.setSuffix(" °F")
        self.step_size_input.setValue(9.0)
        self.hold_time_input = QSpinBox()
        self.hold_time_input.setRange(1, 240)
        self.hold_time_input.setSuffix(" min")
        self.hold_time_input.setValue(60)
        self.min_temp_input = QDoubleSpinBox()
        self.min_temp_input.setRange(-58, 302)
        self.min_temp_input.setSuffix(" °F")
        self.min_temp_input.setValue(32.0)
        self.initial_overheat_input = QDoubleSpinBox()
        self.initial_overheat_input.setRange(0, 90)
        self.initial_overheat_input.setSuffix(" °F")
        self.initial_overheat_input.setValue(18.0)

        self.layout.addLayout(labeled_icon_row("Cloud Point:", "cloud", self.cloud_point_input))
        self.layout.addLayout(labeled_icon_row("Step Size:", "arrow-down", self.step_size_input))
        self.layout.addLayout(labeled_icon_row("Hold Time (min):", "clock", self.hold_time_input))
        self.layout.addLayout(labeled_icon_row("Minimum Temperature:", "thermometer", self.min_temp_input))
        self.layout.addLayout(labeled_icon_row("Initial Overheat:", "fire", self.initial_overheat_input))

        # DATAQ DAQ widgets
        self.daq_com_port_combo = QComboBox()
        self.refresh_daq_ports_button = QPushButton("Refresh DAQ Ports")

        daq_port_row = QHBoxLayout()
        daq_port_row.addWidget(QLabel("DAQ COM Port:"))
        daq_port_row.addWidget(self.daq_com_port_combo)
        daq_port_row.addWidget(self.refresh_daq_ports_button)
        self.layout.addLayout(daq_port_row)

        self.sample_rate_hz_input = QDoubleSpinBox()
        self.sample_rate_hz_input.setRange(0.1, 20.0)
        self.sample_rate_hz_input.setDecimals(2)
        self.sample_rate_hz_input.setSingleStep(0.1)
        self.sample_rate_hz_input.setValue(1.0)
        self.sample_rate_hz_input.setSuffix(" Hz")
        self.layout.addLayout(labeled_icon_row("Logger sample rate:", "view-refresh", self.sample_rate_hz_input))

        channel_layout = QVBoxLayout()
        channel_layout.addWidget(QLabel("Pressure channels (0-based indices for DAQ):"))
        self.channel_inputs = []
        grid = QGridLayout()
        for i in range(4):
            grid.addWidget(QLabel(f"Ch {i+1}:"), i, 0)
            self.channel_inputs.append(QLineEdit(str(i)))
            grid.addWidget(self.channel_inputs[i], i, 1)
        channel_layout.addLayout(grid)
        self.layout.addLayout(channel_layout)

        self.pressure_max_input = QDoubleSpinBox()
        self.pressure_max_input.setRange(0, 1e6)
        self.pressure_max_input.setDecimals(3)
        self.pressure_max_input.setValue(1.0)
        self.layout.addLayout(labeled_icon_row("Pressure max (unit):", "dialog-warning", self.pressure_max_input))

        self.over_pressure_behavior = QComboBox()
        self.over_pressure_behavior.addItems(["Extend Hold", "Pause", "Abort"])
        self.layout.addLayout(labeled_icon_row("Over-pressure behavior:", "process-stop", self.over_pressure_behavior))

        self.start_monitor_button = QPushButton("Start Pressure Monitor")
        self.stop_monitor_button = QPushButton("Stop Pressure Monitor")
        self.stop_monitor_button.setEnabled(False)
        monitor_row = QHBoxLayout()
        monitor_row.addWidget(self.start_monitor_button)
        monitor_row.addWidget(self.stop_monitor_button)
        self.layout.addLayout(monitor_row)

        self.pressure_plot = PressurePlot(channels=4)
        self.layout.addWidget(self.pressure_plot)
        self.layout.addStretch(0)  # Minimal stretch after plot

        # Pressure monitor controls (replaces the table)
        self.view_pressure_button = QPushButton("View Pressure Data")
        self.view_pressure_button.setIcon(QIcon.fromTheme("view-list-details"))
        self.layout.addWidget(self.view_pressure_button)

        # ── Data Logging ──
        self.layout.addWidget(QLabel("── Data Logging ──"))
        log_file_row = QHBoxLayout()
        log_file_row.addWidget(QLabel("Log output file:"))
        self.log_file_input = QLineEdit()
        self.log_file_input.setPlaceholderText("Browse or type a path to create a new CSV...")
        self.log_browse_button = QPushButton("Browse...")
        log_file_row.addWidget(self.log_file_input)
        log_file_row.addWidget(self.log_browse_button)
        self.layout.addLayout(log_file_row)

        log_ctrl_row = QHBoxLayout()
        self.start_log_button = QPushButton("Start Logging")
        self.stop_log_button = QPushButton("Stop Logging")
        self.stop_log_button.setEnabled(False)
        self.log_rows_label = QLabel("Not logging")
        self.log_rows_label.setStyleSheet("color: #83a598;")
        log_ctrl_row.addWidget(self.start_log_button)
        log_ctrl_row.addWidget(self.stop_log_button)
        log_ctrl_row.addWidget(self.log_rows_label)
        self.layout.addLayout(log_ctrl_row)

        self.log_browse_button.clicked.connect(self.browse_log_output)
        self.start_log_button.clicked.connect(self.start_logging)
        self.stop_log_button.clicked.connect(self.stop_logging)

        # Plot scaling controls
        self.autoscale_checkbox = QCheckBox("Autoscale plot")
        self.autoscale_checkbox.setChecked(True)
        self.ymin_input = QDoubleSpinBox()
        self.ymin_input.setRange(-1e6, 1e6)
        self.ymin_input.setDecimals(3)
        self.ymin_input.setValue(0.0)
        self.ymax_input = QDoubleSpinBox()
        self.ymax_input.setRange(-1e6, 1e6)
        self.ymax_input.setDecimals(3)
        self.ymax_input.setValue(1.0)
        self.ymin_input.setEnabled(False)
        self.ymax_input.setEnabled(False)

        scale_row = QHBoxLayout()
        scale_row.addWidget(self.autoscale_checkbox)
        scale_row.addWidget(QLabel("Y min:"))
        scale_row.addWidget(self.ymin_input)
        scale_row.addWidget(QLabel("Y max:"))
        scale_row.addWidget(self.ymax_input)
        self.layout.addLayout(scale_row)

        self.reset_scale_button = QPushButton("Reset Scale")
        self.layout.addWidget(self.reset_scale_button)

        # Config save/load
        self.save_config_button = QPushButton("Save Config")
        self.load_config_button = QPushButton("Load Config")
        config_row = QHBoxLayout()
        config_row.addWidget(self.save_config_button)
        config_row.addWidget(self.load_config_button)
        self.layout.addLayout(config_row)

        self.start_monitor_button.clicked.connect(self.start_pressure_monitor)
        self.stop_monitor_button.clicked.connect(self.stop_pressure_monitor)
        self.autoscale_checkbox.toggled.connect(self.on_autoscale_toggled)
        self.ymin_input.valueChanged.connect(self.on_scale_changed)
        self.ymax_input.valueChanged.connect(self.on_scale_changed)
        self.command_help_button.clicked.connect(self.show_command_reference)
        self.engineering_button.clicked.connect(self.open_engineering_settings)
        self.reset_scale_button.clicked.connect(self.reset_plot_scale)
        self.save_config_button.clicked.connect(self.save_config)
        self.load_config_button.clicked.connect(self.load_config)

        # Control buttons
        btn_row = QHBoxLayout()
        self.start_button = QPushButton("Start Routine")
        self.start_button.setIcon(QIcon.fromTheme("media-playback-start"))
        self.pause_button = QPushButton("Pause")
        self.pause_button.setIcon(QIcon.fromTheme("media-playback-pause"))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(QIcon.fromTheme("process-stop"))
        btn_row.addWidget(self.start_button)
        btn_row.addWidget(self.pause_button)
        btn_row.addWidget(self.stop_button)
        self.layout.addLayout(btn_row)

        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("color: #8ec07c; font-weight: bold;")
        self.layout.addWidget(self.status_label)
        self.layout.addStretch(0)  # Minimal final stretch

        self.start_button.clicked.connect(self.start_routine)
        self.pause_button.clicked.connect(self.pause_resume_routine)
        self.stop_button.clicked.connect(self.stop_routine)
        self.temp_unit_combo.currentTextChanged.connect(self.on_temp_unit_changed)
        self.routine_box.currentIndexChanged.connect(self.update_fields)
        self.temp_unit = "F"
        self.apply_temp_unit("F", convert_values=False)
        self.update_fields()
        self.load_config()
        self.update_plot_scale()
        self.worker = None
        self.monitor_thread = None
        self.paused = False
        self.pressure_dialog = None
        self._data_logger = None
        self._log_timer = None
        self._last_temp = None
        self._last_pressure_values = []

        # Connect the view pressure button
        self.view_pressure_button.clicked.connect(self.show_pressure_dialog)

        # Populate ports
        self.refresh_ports_button.clicked.connect(self.populate_ports)
        self.refresh_daq_ports_button.clicked.connect(self.populate_daq_ports)
        self.test_connection_button.clicked.connect(self.test_connection)
        self.standby_button.clicked.connect(self.toggle_standby)
        self.manual_standby = False
        self.populate_ports()
        self.populate_daq_ports()

    def update_comm_status(self, message, is_ok):
        """Update the communication status label text and color."""
        self.comm_status_label.setText(message)
        if is_ok:
            self.comm_status_label.setStyleSheet("color: #98971a;") # Green
        else:
            self.comm_status_label.setStyleSheet("color: #cc241d;") # Red

    def update_temp_labels(self, current_temp, target_temp):
        """Update the temperature display labels at the top."""
        unit = self.temp_unit_combo.currentText()
        self.current_temp_label.setText(f"Current: {current_temp:.2f}°{unit}")
        self.target_temp_label.setText(f"Target: {target_temp:.2f}°{unit}")
        
    def populate_daq_ports(self):
        """Scan for and populate DAQ COM ports in the dropdown."""
        current_port = self.daq_com_port_combo.currentText()
        self.daq_com_port_combo.clear()
        ports = thermobath_core.get_available_ports()
        if ports:
            self.daq_com_port_combo.addItems(ports)

        # Restore previous selection if it's still available.
        index = self.daq_com_port_combo.findText(current_port)
        if index != -1:
            self.daq_com_port_combo.setCurrentIndex(index)

    def _default_engineering_config(self):
        return [
            {
                "label": f"Ch {i+1}",
                "volt_low": 0.0,
                "volt_high": 5.0,
                "eng_low": 0.0,
                "eng_high": 100.0,
                "eu_label": "EU",
            }
            for i in range(4)
        ]

    def show_command_reference(self):
        dialog = CommandReferenceDialog(self)
        dialog.exec_()

    def open_engineering_settings(self):
        dialog = EngineeringSettingsDialog(self.engineering_config, self)
        if dialog.exec_() == QDialog.Accepted:
            self.engineering_config = dialog.get_settings()
            QMessageBox.information(self, "Engineering Settings", "Engineering settings updated for 4 channels.")

    def set_standby_button_state(self, standby_enabled):
        self.manual_standby = standby_enabled
        if standby_enabled:
            self.standby_button.setText("Standby: On")
            self.standby_button.setStyleSheet("background-color: #5f3a00; color: #f2e5bc;")
        else:
            self.standby_button.setText("Standby: Off")
            self.standby_button.setStyleSheet("")

    def toggle_standby(self):
        """Toggle manual standby mode when no routine is active."""
        if self.worker:
            self.status_label.setText("Status: Routine active. Use Pause for standby during a run.")
            return

        port = self.port_combo.currentText()
        if not port:
            self.status_label.setText("Status: No port selected")
            return

        enable_standby = not self.manual_standby
        self.standby_button.setEnabled(False)
        self.status_label.setText("Status: Updating standby...")

        try:
            bath = thermobath_core.SerialInterface(port)
            bath.write(b"SO 0\r" if enable_standby else b"SO 1\r")
            bath.readline()
            bath.close()

            self.set_standby_button_state(enable_standby)
            state = "ON" if enable_standby else "OFF"
            self.status_label.setText(f"Status: Standby {state}")
            self.update_comm_status(f"Connected to {port}", True)
        except Exception as e:
            self.status_label.setText(f"Status: Standby update failed - {e}")
            self.update_comm_status(f"Connection error: {e}", False)
        finally:
            self.standby_button.setEnabled(True)

    def populate_ports(self):
        """Scan for and populate serial ports in the dropdown."""
        current_port = self.port_combo.currentText()
        self.port_combo.clear()
        ports = thermobath_core.get_available_ports()
        if ports:
            self.port_combo.addItems(ports)

        # Restore previous selection if it's still available
        index = self.port_combo.findText(current_port)
        if index != -1:
            self.port_combo.setCurrentIndex(index)

    def test_connection(self):
        """Test connectivity to the chiller by reading current setpoint."""
        port = self.port_combo.currentText()
        if not port:
            self.status_label.setText("Status: No port selected")
            return
        
        self.status_label.setText("Status: Testing connection...")
        self.test_connection_button.setEnabled(False)
        
        try:
            bath = thermobath_core.SerialInterface(port)

            # Try reading current setpoint to verify communication
            bath.write(b"RS\r")
            response = bath.readline()
            
            if not response:
                self.status_label.setText(f"Status: No response from {port}")
                self.update_comm_status(f"No response from {port}", False)
            else:
                try:
                    setpoint = thermobath_core._response_to_celsius(response, default_unit="F")
                    unit = self.temp_unit_combo.currentText()
                    display_val = c_to_f(setpoint) if unit == "F" else setpoint
                    self.status_label.setText(f"Status: OK - Setpoint: {display_val:.2f}°{unit}")
                    self.update_comm_status(f"Connected to {port}", True)
                except ValueError as e:
                    self.status_label.setText(f"Status: Bad response format: {response!r}")
                    self.update_comm_status(f"Bad response from {port}: {response!r}", False)
            
            bath.close()
        except Exception as e:
            self.status_label.setText(f"Status: Connection failed - {e}")
            self.update_comm_status(f"Connection error: {e}", False)
        finally:
            self.test_connection_button.setEnabled(True)

    def show_pressure_dialog(self):
        """Show the pressure data dialog."""
        if self.pressure_dialog is None:
            self.pressure_dialog = PressureDataDialog(self)
        self.pressure_dialog.show()
        self.pressure_dialog.raise_()
        self.pressure_dialog.activateWindow()

    def browse_log_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Set Data Log Output File",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if path:
            self.log_file_input.setText(path)

    def start_logging(self):
        path = self.log_file_input.text().strip()
        if not path:
            self.status_label.setText("Status: Please set a log output file path first.")
            return
        unit = self.temp_unit_combo.currentText()
        headers = ["Timestamp", f"Temperature (\u00b0{unit})"]
        for i, cfg in enumerate(self.engineering_config):
            label = cfg.get("label", f"Ch {i+1}")
            eu = cfg.get("eu_label", "EU")
            headers.append(f"{label} ({eu})")
        try:
            self._data_logger = thermobath_core.CsvDataLogger(path, headers)
        except Exception as e:
            self.status_label.setText(f"Status: Failed to start logger: {e}")
            return
        interval_ms = max(50, int(1000.0 / self.sample_rate_hz_input.value()))
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(interval_ms)
        self._log_timer.timeout.connect(self._write_log_row)
        self._log_timer.start()
        self.start_log_button.setEnabled(False)
        self.stop_log_button.setEnabled(True)
        self.log_rows_label.setText("Logging — 0 rows")

    def stop_logging(self):
        if self._log_timer is not None:
            self._log_timer.stop()
            self._log_timer = None
        if self._data_logger is not None:
            rows = self._data_logger.row_count
            self._data_logger.close()
            self._data_logger = None
            self.log_rows_label.setText(f"Stopped — {rows} rows written")
        self.start_log_button.setEnabled(True)
        self.stop_log_button.setEnabled(False)

    def _update_last_temp(self, current_temp, _target_temp):
        self._last_temp = current_temp

    def _write_log_row(self):
        if self._data_logger is None:
            return
        ts = datetime.now().isoformat(timespec="milliseconds")
        temp_str = f"{self._last_temp:.4f}" if self._last_temp is not None else ""
        pressure_strs = [f"{v:.4f}" if v is not None else "" for v in self._last_pressure_values]
        while len(pressure_strs) < 4:
            pressure_strs.append("")
        row = [ts, temp_str] + pressure_strs[:4]
        try:
            self._data_logger.write_row(row)
            self.log_rows_label.setText(f"Logging — {self._data_logger.row_count} rows")
        except Exception as e:
            self.log_rows_label.setText(f"Log error: {e}")

    def start_pressure_monitor(self):
        if self.monitor_thread and self.monitor_thread.isRunning():
            return
        
        base_reader = None

        com_port = self.daq_com_port_combo.currentText().strip()
        if not com_port:
            self.status_label.setText("Status: No DAQ COM port selected.")
            QMessageBox.critical(self, "DAQ Port Required", "Please select a DAQ COM port.")
            return

        try:
            base_reader = daq.DataqSerialPressureSource(com_port=com_port)
        except Exception as e:
            self.status_label.setText(f"Status: Failed to open DAQ: {e}")
            QMessageBox.critical(self, "DAQ Error", f"Failed to initialize DATAQ DAQ on {com_port}.\\n\\n{e}")
            return
        
        if not base_reader:
            self.status_label.setText("Status: Could not create a pressure reader.")
            return

        channels = []
        channel_names = []
        for edit in self.channel_inputs:
            ch_input = edit.text().strip()
            try:
                channels.append(int(ch_input))
                channel_names.append(f"Ch {int(ch_input) + 1}")
            except ValueError:
                self.status_label.setText(f"Status: Invalid channel index '{ch_input}' for DAQ.")
                return

        channel_map = {}
        for i, ch in enumerate(channels):
            if i < len(self.engineering_config):
                channel_map[str(ch)] = self.engineering_config[i]

        reader = thermobath_core.ChannelEngineeringPressureSource(base_reader, channel_map)
        interval_s = 1.0 / self.sample_rate_hz_input.value()

        display_names = []
        for i, name in enumerate(channel_names):
            cfg = self.engineering_config[i] if i < len(self.engineering_config) else {}
            label = cfg.get("label") or name
            unit = cfg.get("eu_label", "EU")
            display_names.append(f"{label} ({unit})")

        self.monitor_thread = daq.DAQMonitorThread(reader, channels, display_names, interval_s=interval_s)

        self.monitor_thread.data_updated.connect(self.pressure_plot.update_values)
        self.monitor_thread.table_updated.connect(self.update_pressure_table)
        self.monitor_thread.status_updated.connect(self.status_label.setText)
        self.monitor_thread.start()
        self.start_monitor_button.setEnabled(False)
        self.stop_monitor_button.setEnabled(True)
        self.status_label.setText(f"Status: Monitoring pressure at {self.sample_rate_hz_input.value():.2f} Hz...")

    def stop_pressure_monitor(self):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread.wait(2000)
            self.monitor_thread = None
        self.start_monitor_button.setEnabled(True)
        self.stop_monitor_button.setEnabled(False)
        self.status_label.setText("Status: Pressure monitor stopped.")

    def on_autoscale_toggled(self, enabled):
        self.ymin_input.setEnabled(not enabled)
        self.ymax_input.setEnabled(not enabled)
        self.update_plot_scale()

    def on_scale_changed(self):
        if not self.autoscale_checkbox.isChecked():
            self.update_plot_scale()

    def reset_plot_scale(self):
        self.autoscale_checkbox.setChecked(True)
        self.ymin_input.setValue(0.0)
        self.ymax_input.setValue(1.0)
        self.update_plot_scale()

    def update_plot_scale(self):
        autoscale = self.autoscale_checkbox.isChecked()
        ymin = self.ymin_input.value()
        ymax = self.ymax_input.value()
        self.pressure_plot.set_scale(autoscale=autoscale, y_min=ymin, y_max=ymax)

    def update_pressure_table(self, channel_names, values):
        """Update the pressure dialog with new data if it's open."""
        if self.pressure_dialog and self.pressure_dialog.isVisible():
            self.pressure_dialog.update_pressure_data(channel_names, values)
        self._last_pressure_values = list(values)

    def update_fields(self):
        routine = self.routine_box.currentText()
        show_stepwise = routine == "Stepwise"
        self.cloud_point_input.setVisible(show_stepwise)
        self.step_size_input.setVisible(show_stepwise)
        self.hold_time_input.setVisible(show_stepwise)
        self.min_temp_input.setVisible(show_stepwise)
        self.initial_overheat_input.setVisible(show_stepwise)
        self.constant_temp_input.setVisible(not show_stepwise)

    def _temperature_spins(self):
        return [
            self.constant_temp_input,
            self.cloud_point_input,
            self.step_size_input,
            self.min_temp_input,
            self.initial_overheat_input,
        ]

    def apply_temp_unit(self, unit, convert_values=True):
        old_unit = getattr(self, "temp_unit", "F")
        if convert_values and old_unit != unit:
            for spin in self._temperature_spins():
                value = spin.value()
                if unit == "C":
                    spin.setValue(f_to_c(value))
                else:
                    spin.setValue(c_to_f(value))

            current = self.gauge.current_temp
            target = self.gauge.target_temp
            if unit == "C":
                self.gauge.set_temps(f_to_c(current), f_to_c(target))
            else:
                self.gauge.set_temps(c_to_f(current), c_to_f(target))

        self.temp_unit = unit
        self.gauge.set_unit(unit)

        if unit == "F":
            self.constant_temp_input.setRange(-58, 302)
            self.cloud_point_input.setRange(-58, 302)
            self.step_size_input.setRange(0.1, 90)
            self.min_temp_input.setRange(-58, 302)
            self.initial_overheat_input.setRange(0, 90)
        else:
            self.constant_temp_input.setRange(-50, 150)
            self.cloud_point_input.setRange(-50, 150)
            self.step_size_input.setRange(0.1, 50)
            self.min_temp_input.setRange(-50, 150)
            self.initial_overheat_input.setRange(0, 50)

        for spin in self._temperature_spins():
            spin.setSuffix(f" {chr(176)}{unit}")

        # Keep top readouts synchronized with the gauge after unit changes.
        self.update_temp_labels(self.gauge.current_temp, self.gauge.target_temp)

    def on_temp_unit_changed(self, unit):
        self.apply_temp_unit(unit, convert_values=True)

    def legacy_config_path(self):
        """Return legacy bundled config path for fallback/defaults."""
        return Path(resource_path("config/ui_settings.json"))

    def config_path(self):
        """Return writable user config path for the UI config file."""
        return user_config_dir() / "ui_settings.json"

    def load_config(self):
        """Load UI settings from disk."""
        path = self.config_path()
        if not path.exists():
            path = self.legacy_config_path()
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            return

        saved_port = config.get("port", "")
        if saved_port:
            self.port_combo.setCurrentText(saved_port)
        unit = config.get("temp_unit", "F")
        self.temp_unit_combo.blockSignals(True)
        self.temp_unit_combo.setCurrentText(unit)
        self.temp_unit_combo.blockSignals(False)
        self.apply_temp_unit(unit, convert_values=False)

        saved_daq_port = config.get("daq_com_port", "")
        if saved_daq_port:
            self.daq_com_port_combo.setCurrentText(saved_daq_port)
        self.sample_rate_hz_input.setValue(config.get("sample_rate_hz", 1.0))
        self.log_file_input.setText(config.get("log_file", ""))
        channels = config.get("channels", ["0", "1", "2", "3"])
        for spin, ch in zip(self.channel_inputs, channels):
            spin.setText(str(ch))
        loaded_engineering = config.get("engineering_channels")
        if isinstance(loaded_engineering, list) and len(loaded_engineering) == 4:
            self.engineering_config = loaded_engineering
        self.pressure_max_input.setValue(config.get("pressure_max", 1.0))
        self.over_pressure_behavior.setCurrentText(config.get("over_pressure_behavior", "Extend Hold"))

        autoscale = config.get("autoscale", True)
        self.autoscale_checkbox.setChecked(autoscale)
        self.ymin_input.setValue(config.get("plot_min", 0.0))
        self.ymax_input.setValue(config.get("plot_max", 1.0))
        self.update_plot_scale()

    def save_config(self):
        """Persist UI settings to disk."""
        config = {
            "port": self.port_combo.currentText(),
            "temp_unit": self.temp_unit_combo.currentText(),
            "daq_com_port": self.daq_com_port_combo.currentText(),
            "sample_rate_hz": self.sample_rate_hz_input.value(),
            "log_file": self.log_file_input.text().strip(),
            "channels": [edit.text().strip() for edit in self.channel_inputs],
            "engineering_channels": self.engineering_config,
            "pressure_max": self.pressure_max_input.value(),
            "over_pressure_behavior": self.over_pressure_behavior.currentText(),
            "autoscale": self.autoscale_checkbox.isChecked(),
            "plot_min": self.ymin_input.value(),
            "plot_max": self.ymax_input.value(),
        }
        try:
            path = self.config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_logging()
        self.save_config()
        super().closeEvent(event)

    def start_routine(self):
        routine = self.routine_box.currentText()
        port = self.port_combo.currentText()
        unit = self.temp_unit_combo.currentText()

        if routine == "Constant":
            set_temp_display = self.constant_temp_input.value()
            set_temp_c = f_to_c(set_temp_display) if unit == "F" else set_temp_display
            params = {'set_temp': set_temp_c}
            # Do not overwrite current temp with target before RT polling starts.
            self.gauge.set_temps(self.gauge.current_temp, set_temp_display)
            self.update_temp_labels(self.gauge.current_temp, set_temp_display)
        else:
            cloud_display = self.cloud_point_input.value()
            step_size_display = self.step_size_input.value()
            min_display = self.min_temp_input.value()
            overheat_display = self.initial_overheat_input.value()
            factor = 5.0 / 9.0 if unit == "F" else 1.0
            params = {
                'cloud_point': f_to_c(cloud_display) if unit == "F" else cloud_display,
                'step_size': step_size_display * factor,
                'hold_time': self.hold_time_input.value(),
                'min_temp': f_to_c(min_display) if unit == "F" else min_display,
                'initial_overheat': overheat_display * factor
            }
            self.gauge.set_temps(self.gauge.current_temp, cloud_display + overheat_display)
            self.update_temp_labels(self.gauge.current_temp, cloud_display + overheat_display)
        self.status_label.setText("Status: Connecting...")

        pressure_reader = None
        pressure_channels = None
        pressure_max = None
        pressure_check_interval = 5
        if self.monitor_thread and self.monitor_thread.isRunning():
            pressure_reader = self.monitor_thread.reader
            pressure_channels = self.monitor_thread.channels
            pressure_max = self.pressure_max_input.value()
            pressure_check_interval = 1

        self.worker = BathWorker(
            routine,
            params,
            port,
            temp_unit=unit,
            pressure_reader=pressure_reader,
            pressure_channels=pressure_channels,
            pressure_max=pressure_max,
            pressure_check_interval=pressure_check_interval,
            over_pressure_behavior=self.over_pressure_behavior.currentText(),
            parent_widget=self,
        )
        self.worker.comm_status.connect(self.update_comm_status)
        self.worker.update_status.connect(self.status_label.setText)
        self.worker.update_gauge.connect(self.gauge.set_temps)
        self.worker.update_gauge.connect(self.update_temp_labels)
        self.worker.update_gauge.connect(self._update_last_temp)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()
        self.paused = False
        self.pause_button.setText("Pause")

    def pause_resume_routine(self):
        if not self.worker:
            return
        if not self.paused:
            self.worker.pause()
            self.paused = True
            self.pause_button.setText("Resume")
            self.status_label.setText("Status: Paused (Standby)")
        else:
            self.worker.resume()
            self.paused = False
            self.pause_button.setText("Pause")
            self.status_label.setText("Status: Running...")

    def stop_routine(self):
        if self.worker:
            self.worker.stop()
            self.status_label.setText("Status: Stopping...")

    def on_worker_finished(self):
        self.status_label.setText("Status: Done")
        self.worker = None
        self.paused = False
        self.pause_button.setText("Pause")

        self.monitor_thread = None

def labeled_icon_row(label, icon_name, widget):
    row = QHBoxLayout()
    icon = QIcon.fromTheme(icon_name)
    icon_label = QLabel()
    if not icon.isNull():
        icon_label.setPixmap(icon.pixmap(20, 20))
    row.addWidget(icon_label)
    row.addWidget(QLabel(label))
    row.addWidget(widget)
    return row

def dark_stylesheet():
    return """
    QWidget {
        background-color: #232629;
        color: #e0e0e0;
        font-family: Segoe UI, Arial, sans-serif;
        font-size: 13px;
    }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #2d2f31;
        color: #e0e0e0;
        border: 1px solid #444;
        border-radius: 4px;
        padding: 2px 4px;
    }
    QPushButton {
        background-color: #3c3f41;
        color: #e0e0e0;
        border: 1px solid #555;
        border-radius: 4px;
        padding: 6px 12px;
    }
    QPushButton:hover {
        background-color: #505357;
    }
    QLabel {
        color: #e0e0e0;
    }
    QComboBox QAbstractItemView {
        background-color: #232629;
        color: #e0e0e0;
        selection-background-color: #444;
    }
    """
    
    
def main():
    app = QApplication(sys.argv)
    # Set app icon
    # app_icon = QIcon(resource_path("icon.ico"))
    # app.setWindowIcon(app_icon)
    window = ThermobathUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()