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
    QStackedWidget,
    QGridLayout,
    QSizePolicy,
    QDialog,
    QMessageBox,
    QGroupBox,
    QFrame,
)

from PyQt5.QtGui import QIcon, QPainter, QPen, QColor, QFont, QConicalGradient, QRadialGradient, QLinearGradient, QPainterPath
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
        self.setMinimumSize(300, 300)
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
        painter.setRenderHint(QPainter.TextAntialiasing)

        size = min(self.width(), self.height())
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        outer_radius = (size / 2.0) - 10
        dial_rect = QRectF(
            center_x - outer_radius,
            center_y - outer_radius,
            outer_radius * 2,
            outer_radius * 2,
        )

        span_degrees = 270.0
        start_degrees = 135.0

        def temp_ratio(value):
            ratio = (value - self.min_display_temp) / (self.max_display_temp - self.min_display_temp)
            return max(0.0, min(1.0, ratio))

        target_ratio = temp_ratio(self.target_temp)
        current_ratio = temp_ratio(self.current_temp)

        def point_on_circle(radius, angle_degrees):
            radians = math.radians(angle_degrees)
            return QPointF(
                center_x + math.cos(radians) * radius,
                center_y + math.sin(radians) * radius,
            )

        bezel_gradient = QRadialGradient(dial_rect.center(), outer_radius)
        bezel_gradient.setColorAt(0.0, QColor(82, 96, 112))
        bezel_gradient.setColorAt(0.55, QColor(28, 38, 50))
        bezel_gradient.setColorAt(1.0, QColor(6, 10, 15))
        painter.setPen(Qt.NoPen)
        painter.setBrush(bezel_gradient)
        painter.drawEllipse(dial_rect)

        bezel_ring_pen = QPen(QColor(122, 142, 164, 110), max(2, int(size * 0.01)))
        painter.setPen(bezel_ring_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(dial_rect.adjusted(6, 6, -6, -6))

        inner_radius = outer_radius * 0.845
        inner_rect = QRectF(
            center_x - inner_radius,
            center_y - inner_radius,
            inner_radius * 2,
            inner_radius * 2,
        )
        face_gradient = QRadialGradient(inner_rect.center(), inner_radius)
        face_gradient.setColorAt(0.0, QColor(24, 34, 46, 245))
        face_gradient.setColorAt(0.58, QColor(12, 20, 29, 240))
        face_gradient.setColorAt(1.0, QColor(4, 9, 15, 248))
        painter.setBrush(face_gradient)
        painter.drawEllipse(inner_rect)

        tick_radius = inner_radius * 0.90
        painter.save()
        painter.translate(inner_rect.center())
        for tick_index in range(50):
            angle = -135.0 + (span_degrees / 49.0) * tick_index
            painter.save()
            painter.rotate(angle)
            tick_pen = QPen(QColor(186, 213, 232, 80 if tick_index % 5 else 155))
            tick_pen.setWidthF(1.8 if tick_index % 5 else 3.2)
            tick_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(tick_pen)
            tick_start = QPointF(0, -tick_radius)
            tick_end = QPointF(0, -(tick_radius - (18 if tick_index % 5 == 0 else 9)))
            painter.drawLine(tick_start, tick_end)
            painter.restore()
        painter.restore()

        label_font = QFont("Segoe UI")
        label_font.setPixelSize(max(10, int(size * 0.033)))
        label_font.setWeight(QFont.Medium)
        painter.setFont(label_font)
        painter.setPen(QColor(122, 144, 164))
        for label_ratio in [0.0, 0.25, 0.5, 0.75, 1.0]:
            angle = start_degrees + (span_degrees * label_ratio)
            label_temp = self.min_display_temp + ((self.max_display_temp - self.min_display_temp) * label_ratio)
            label_point = point_on_circle(inner_radius * 0.72, angle)
            label_rect = QRectF(label_point.x() - 26, label_point.y() - 10, 52, 20)
            painter.drawText(label_rect, Qt.AlignCenter, f"{label_temp:.0f}")

        track_radius = inner_radius * 0.71
        track_rect = QRectF(
            center_x - track_radius,
            center_y - track_radius,
            track_radius * 2,
            track_radius * 2,
        )
        track_pen = QPen(QColor(110, 140, 165, 48), 15)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(track_rect, int(start_degrees * 16), int(-span_degrees * 16))

        target_gradient = QConicalGradient(track_rect.center(), -135)
        target_gradient.setColorAt(0.00, QColor(64, 240, 255, 30))
        target_gradient.setColorAt(0.18, QColor(88, 232, 255, 255))
        target_gradient.setColorAt(0.50, QColor(52, 175, 255, 255))
        target_gradient.setColorAt(0.82, QColor(20, 72, 122, 90))
        target_gradient.setColorAt(1.00, QColor(64, 240, 255, 30))

        target_glow_pen = QPen(QColor(56, 232, 255, 70), 24)
        target_glow_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(target_glow_pen)
        painter.drawArc(track_rect, int(start_degrees * 16), int(-(span_degrees * target_ratio) * 16))

        target_pen = QPen(target_gradient, 15)
        target_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(target_pen)
        painter.drawArc(track_rect, int(start_degrees * 16), int(-(span_degrees * target_ratio) * 16))

        current_gradient = QConicalGradient(track_rect.center(), -135)
        current_gradient.setColorAt(0.00, QColor(20, 255, 170, 30))
        current_gradient.setColorAt(0.16, QColor(72, 255, 182, 255))
        current_gradient.setColorAt(0.45, QColor(0, 232, 144, 255))
        current_gradient.setColorAt(0.80, QColor(10, 84, 56, 100))
        current_gradient.setColorAt(1.00, QColor(20, 255, 170, 30))

        current_glow_pen = QPen(QColor(46, 255, 170, 80), 24)
        current_glow_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(current_glow_pen)
        painter.drawArc(track_rect, int(start_degrees * 16), int(-(span_degrees * current_ratio) * 16))

        current_pen = QPen(current_gradient, 15)
        current_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(current_pen)
        painter.drawArc(track_rect, int(start_degrees * 16), int(-(span_degrees * current_ratio) * 16))

        target_angle = start_degrees + (span_degrees * target_ratio)
        target_outer = point_on_circle(track_radius + 16, target_angle)
        target_inner = point_on_circle(track_radius + 1, target_angle)
        target_bug_pen = QPen(QColor(116, 236, 255, 220), 3)
        target_bug_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(target_bug_pen)
        painter.drawLine(target_outer, target_inner)
        painter.setBrush(QColor(116, 236, 255, 220))
        painter.drawEllipse(target_outer, 4.0, 4.0)

        current_angle = start_degrees + (span_degrees * current_ratio)
        current_marker = point_on_circle(track_radius - 1, current_angle)
        painter.setBrush(QColor(92, 255, 178, 220))
        painter.setPen(QPen(QColor(92, 255, 178, 90), 6))
        painter.drawEllipse(current_marker, 4.5, 4.5)

        core_radius = inner_radius * 0.53
        core_rect = QRectF(
            center_x - core_radius,
            center_y - core_radius,
            core_radius * 2,
            core_radius * 2,
        )
        core_gradient = QRadialGradient(core_rect.center(), core_radius)
        core_gradient.setColorAt(0.0, QColor(29, 43, 56, 245))
        core_gradient.setColorAt(0.75, QColor(12, 19, 27, 240))
        core_gradient.setColorAt(1.0, QColor(5, 9, 14, 250))
        painter.setPen(QPen(QColor(120, 155, 180, 45), 1))
        painter.setBrush(core_gradient)
        painter.drawEllipse(core_rect)

        current_font = QFont("Segoe UI")
        current_font.setPixelSize(max(38, int(size * 0.15)))
        current_font.setWeight(QFont.Light)
        painter.setFont(current_font)
        painter.setPen(QColor(240, 248, 255))
        current_text_rect = QRectF(core_rect.left(), core_rect.top() + size * 0.02, core_rect.width(), core_rect.height() * 0.48)
        painter.drawText(current_text_rect, Qt.AlignCenter, f"{self.current_temp:.1f}°{self.unit}")

        readout_font = QFont("Segoe UI")
        readout_font.setPixelSize(max(14, int(size * 0.048)))
        readout_font.setWeight(QFont.Medium)
        painter.setFont(readout_font)
        painter.setPen(QColor(126, 202, 216))
        target_text_rect = QRectF(core_rect.left(), core_rect.center().y() + 4, core_rect.width(), core_rect.height() * 0.22)
        painter.drawText(target_text_rect, Qt.AlignHCenter | Qt.AlignTop, f"TARGET {self.target_temp:.1f}°{self.unit}")

        sub_font = QFont("Segoe UI")
        sub_font.setPixelSize(max(11, int(size * 0.034)))
        sub_font.setWeight(QFont.Normal)
        painter.setFont(sub_font)
        painter.setPen(QColor(110, 130, 150))
        status_rect = QRectF(core_rect.left(), core_rect.bottom() - core_rect.height() * 0.18, core_rect.width(), core_rect.height() * 0.12)
        painter.drawText(status_rect, Qt.AlignHCenter | Qt.AlignVCenter, "AEROSPACE SUPERVISORY DIAL")


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


class SparklineWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = []
        self.setMinimumHeight(32)
        self.setMaximumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_samples(self, samples):
        self.samples = list(samples)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 4, -2, -4)

        if len(self.samples) < 2:
            painter.setPen(QPen(QColor(90, 110, 126, 90), 1))
            painter.drawLine(rect.left(), rect.center().y(), rect.right(), rect.center().y())
            return

        vmin = min(self.samples)
        vmax = max(self.samples)
        if vmax <= vmin:
            vmax = vmin + 1.0

        path = QPainterPath()
        for index, sample in enumerate(self.samples):
            x = rect.left() + (index / max(1, len(self.samples) - 1)) * rect.width()
            y = rect.bottom() - ((sample - vmin) / (vmax - vmin)) * rect.height()
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)

        fill_path = QPainterPath(path)
        fill_path.lineTo(rect.right(), rect.bottom())
        fill_path.lineTo(rect.left(), rect.bottom())
        fill_path.closeSubpath()

        fill_gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill_gradient.setColorAt(0.0, QColor(78, 222, 255, 70))
        fill_gradient.setColorAt(1.0, QColor(78, 222, 255, 4))
        painter.fillPath(fill_path, fill_gradient)

        glow_pen = QPen(QColor(80, 228, 255, 55), 5)
        glow_pen.setCapStyle(Qt.RoundCap)
        glow_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        line_pen = QPen(QColor(112, 236, 255), 2)
        line_pen.setCapStyle(Qt.RoundCap)
        line_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(line_pen)
        painter.drawPath(path)


class KPICard(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(138)
        self.history = []
        self.history_len = 28
        self._severity = "normal"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #8aa6bf; font-size: 13px; font-weight: 600; border: none;")

        self.value_label = QLabel("-- EU")
        self.value_label.setStyleSheet("color: #f5fbff; font-size: 24px; font-weight: 700; border: none;")

        self.trend_label = QLabel("dP/dt: -- /s")
        self.trend_label.setStyleSheet("color: #65d7b1; font-size: 12px; border: none;")

        self.sparkline = SparklineWidget()

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.trend_label)
        layout.addWidget(self.sparkline)
        layout.addStretch(1)
        self._apply_card_style("normal")

    def set_title(self, title):
        self.title_label.setText(title)

    def _apply_card_style(self, severity):
        styles = {
            "normal": (
                "rgba(18, 25, 33, 0.96)",
                "rgba(92, 118, 142, 0.55)",
                "rgba(85, 220, 198, 0.18)",
            ),
            "warning": (
                "rgba(38, 28, 18, 0.96)",
                "rgba(208, 133, 46, 0.8)",
                "rgba(255, 173, 79, 0.22)",
            ),
            "alarm": (
                "rgba(42, 18, 23, 0.97)",
                "rgba(225, 93, 115, 0.9)",
                "rgba(225, 93, 115, 0.25)",
            ),
        }
        bg, border, glow = styles.get(severity, styles["normal"])
        self.setStyleSheet(
            "QWidget#kpiCard {"
            f"background-color: {bg};"
            f"border: 1px solid {border};"
            "border-radius: 14px;"
            f"selection-background-color: {glow};"
            "}"
        )
        self._severity = severity

    def update_metrics(self, value, unit="EU", rate=None, threshold=None):
        if value is None:
            self.value_label.setText(f"-- {unit}")
        else:
            self.value_label.setText(f"{value:.2f} {unit}")
            self.history.append(value)
            if len(self.history) > self.history_len:
                self.history.pop(0)
            self.sparkline.set_samples(self.history)

        severity = "normal"
        if threshold is not None and threshold > 0 and value is not None:
            if value >= threshold:
                severity = "alarm"
            elif value >= threshold * 0.9:
                severity = "warning"
        self._apply_card_style(severity)

        if rate is None:
            self.trend_label.setText("-  dP/dt: -- /s")
            self.trend_label.setStyleSheet("color: #7aa2c0; font-size: 12px; border: none;")
        else:
            if rate > 0.01:
                trend_prefix = "↑"
                trend_color = "#ffb067"
            elif rate < -0.01:
                trend_prefix = "↓"
                trend_color = "#6fe0ff"
            else:
                trend_prefix = "→"
                trend_color = "#65d7b1"
            self.trend_label.setText(f"{trend_prefix}  dP/dt: {rate:+.4f} /s")
            self.trend_label.setStyleSheet(
                f"color: {trend_color}; font-size: 12px; border: none;"
            )


class ProfilePreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "Stepwise"
        self.constant_temp = 77.0
        self.cloud_point = 50.0
        self.step_size = 9.0
        self.hold_time = 60.0
        self.min_temp = 32.0
        self.initial_overheat = 18.0
        self.dp_dt_threshold = 0.01
        self.monitor_window = 30.0
        self.setMinimumSize(320, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_profile_params(
        self,
        mode,
        constant_temp,
        cloud_point,
        step_size,
        hold_time,
        min_temp,
        initial_overheat,
        dp_dt_threshold,
        monitor_window,
    ):
        self.mode = mode
        self.constant_temp = constant_temp
        self.cloud_point = cloud_point
        self.step_size = max(0.1, step_size)
        self.hold_time = max(1.0, hold_time)
        self.min_temp = min_temp
        self.initial_overheat = max(0.0, initial_overheat)
        self.dp_dt_threshold = max(0.0, dp_dt_threshold)
        self.monitor_window = max(1.0, monitor_window)
        self.update()

    def _build_stepwise_profile(self):
        start_temp = self.cloud_point
        overheat_temp = self.cloud_point + self.initial_overheat
        ramp_time = max(10.0, self.hold_time * 0.35)
        settle_time = max(8.0, self.hold_time * 0.18)
        drop_time = max(6.0, self.hold_time * 0.12)

        profile_points = [(0.0, start_temp)]
        current_time = ramp_time
        profile_points.append((current_time, overheat_temp))
        current_time += settle_time
        profile_points.append((current_time, overheat_temp))
        current_time += drop_time
        current_temp = self.cloud_point
        profile_points.append((current_time, current_temp))

        plateaus = []
        plateau_index = 1
        while current_temp > self.min_temp:
            plateau_start = current_time
            current_time += self.hold_time
            profile_points.append((current_time, current_temp))
            plateaus.append((plateau_start, current_time, current_temp, f"P{plateau_index}"))
            plateau_index += 1

            next_temp = max(self.min_temp, current_temp - self.step_size)
            current_time += max(4.0, self.hold_time * 0.08)
            profile_points.append((current_time, next_temp))
            current_temp = next_temp

        if profile_points[-1][0] < current_time + self.hold_time * 0.5:
            plateau_start = current_time
            current_time += self.hold_time * 0.5
            profile_points.append((current_time, current_temp))
            plateaus.append((plateau_start, current_time, current_temp, f"P{plateau_index}"))

        markers = [
            (0.0, start_temp, "Start"),
            (ramp_time, overheat_temp, "Overheat"),
            (ramp_time + settle_time + drop_time, self.cloud_point, "Cloud Pt"),
            (current_time, current_temp, "Min Temp"),
        ]
        return profile_points, plateaus, markers, QColor(92, 228, 255), "Stepwise Profile Preview"

    def _build_constant_profile(self):
        total_time = 120.0
        points = [(0.0, self.constant_temp), (total_time, self.constant_temp)]
        markers = [
            (0.0, self.constant_temp, "Setpoint"),
            (total_time, self.constant_temp, "Hold"),
        ]
        return points, [], markers, QColor(116, 255, 178), "Constant Hold Preview"

    def _build_smart_dynamic_profile(self):
        points, plateaus, markers, _color, _title = self._build_stepwise_profile()
        dynamic_markers = list(markers)
        dynamic_markers.append((points[min(len(points) - 1, 4)][0], points[min(len(points) - 1, 4)][1], "dP/dt Monitor"))
        return points, plateaus, dynamic_markers, QColor(255, 186, 92), "Smart Dynamic Preview"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        rect = self.rect().adjusted(16, 16, -16, -16)
        painter.fillRect(rect, QColor(16, 23, 31))

        frame_pen = QPen(QColor(57, 74, 90), 1)
        painter.setPen(frame_pen)
        painter.drawRoundedRect(rect, 14, 14)

        plot_rect = rect.adjusted(48, 18, -18, -42)
        axis_pen = QPen(QColor(92, 113, 132), 1.5)
        painter.setPen(axis_pen)
        painter.drawLine(plot_rect.left(), plot_rect.bottom(), plot_rect.right(), plot_rect.bottom())
        painter.drawLine(plot_rect.left(), plot_rect.top(), plot_rect.left(), plot_rect.bottom())

        grid_pen = QPen(QColor(54, 70, 85), 1, Qt.DashLine)
        painter.setPen(grid_pen)
        for i in range(1, 4):
            y = plot_rect.top() + (plot_rect.height() * i) / 4.0
            painter.drawLine(plot_rect.left(), int(y), plot_rect.right(), int(y))
        for i in range(1, 5):
            x = plot_rect.left() + (plot_rect.width() * i) / 5.0
            painter.drawLine(int(x), plot_rect.top(), int(x), plot_rect.bottom())

        if self.mode == "Constant":
            profile_points, plateaus, markers, accent_color, title = self._build_constant_profile()
        elif self.mode == "Smart Dynamic":
            profile_points, plateaus, markers, accent_color, title = self._build_smart_dynamic_profile()
        else:
            profile_points, plateaus, markers, accent_color, title = self._build_stepwise_profile()

        max_time = max(point[0] for point in profile_points)
        temps = [point[1] for point in profile_points]
        temp_min = min(min(temps), self.min_temp) - 4.0
        temp_max = max(max(temps), self.cloud_point + self.initial_overheat, self.constant_temp) + 4.0
        if temp_max <= temp_min:
            temp_max = temp_min + 1.0

        def to_point(time_value, temp_value):
            x = plot_rect.left() + (time_value / max(1.0, max_time)) * plot_rect.width()
            y_ratio = (temp_value - temp_min) / (temp_max - temp_min)
            y = plot_rect.bottom() - (y_ratio * plot_rect.height())
            return QPointF(x, y)

        mapped_points = [to_point(time_value, temp_value) for time_value, temp_value in profile_points]

        if self.mode == "Smart Dynamic":
            monitor_width = (self.monitor_window / max(1.0, max_time)) * plot_rect.width()
            monitor_x = mapped_points[min(len(mapped_points) - 1, 3)].x()
            monitor_rect = QRectF(monitor_x, plot_rect.top(), min(monitor_width, plot_rect.right() - monitor_x), plot_rect.height())
            painter.fillRect(monitor_rect, QColor(255, 186, 92, 24))
            painter.setPen(QPen(QColor(255, 186, 92, 110), 1, Qt.DashLine))
            painter.drawRect(monitor_rect)

        for plateau_start, plateau_end, plateau_temp, plateau_label in plateaus[:4]:
            start_point = to_point(plateau_start, plateau_temp)
            end_point = to_point(plateau_end, plateau_temp)
            label_rect = QRectF(start_point.x(), start_point.y() - 20, max(40, end_point.x() - start_point.x()), 16)
            painter.setPen(QColor(140, 163, 183))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(label_rect, Qt.AlignCenter, plateau_label)

        glow_pen = QPen(QColor(accent_color.red(), accent_color.green(), accent_color.blue(), 70), 8)
        glow_pen.setCapStyle(Qt.RoundCap)
        glow_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(glow_pen)
        for a, b in zip(mapped_points, mapped_points[1:]):
            painter.drawLine(a, b)

        profile_pen = QPen(accent_color, 3)
        profile_pen.setCapStyle(Qt.RoundCap)
        profile_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(profile_pen)
        for a, b in zip(mapped_points, mapped_points[1:]):
            painter.drawLine(a, b)

        node_pen = QPen(QColor(180, 246, 255), 1)
        painter.setPen(node_pen)
        painter.setBrush(accent_color)
        for point in mapped_points:
            painter.drawEllipse(point, 3.5, 3.5)

        marker_font = QFont("Segoe UI", 8)
        painter.setFont(marker_font)
        for time_value, temp_value, marker_text in markers:
            point = to_point(time_value, temp_value)
            painter.setPen(QPen(QColor(220, 235, 245), 1))
            painter.setBrush(QColor(220, 235, 245))
            painter.drawEllipse(point, 3.0, 3.0)
            text_rect = QRectF(point.x() - 42, point.y() - 26, 84, 16)
            painter.setPen(QColor(176, 198, 218))
            painter.drawText(text_rect, Qt.AlignCenter, marker_text)

        label_font = QFont("Segoe UI", 9)
        painter.setFont(label_font)
        painter.setPen(QColor(150, 171, 191))
        painter.drawText(rect.adjusted(12, 6, -12, -6), Qt.AlignTop | Qt.AlignLeft, title)
        painter.drawText(plot_rect.left() - 30, plot_rect.top() + 6, f"{temp_max:.0f}")
        painter.drawText(plot_rect.left() - 30, plot_rect.bottom(), f"{temp_min:.0f}")
        painter.drawText(plot_rect.right() - 40, plot_rect.bottom() + 24, f"{max_time:.0f}m")
        painter.drawText(plot_rect.left(), plot_rect.bottom() + 24, "0m")
        painter.drawText(plot_rect.left() - 10, rect.top() + 22, "Temp")
        painter.drawText(plot_rect.right() - 28, rect.bottom() - 12, "Time")

        info_rect = QRectF(rect.left() + 12, rect.bottom() - 28, rect.width() - 24, 18)
        painter.setPen(QColor(114, 135, 156))
        if self.mode == "Smart Dynamic":
            info_text = f"dP/dt threshold {self.dp_dt_threshold:.4f}/s | monitor {self.monitor_window:.0f}s"
        elif self.mode == "Constant":
            info_text = f"Setpoint hold at {self.constant_temp:.1f}"
        else:
            info_text = f"Step {self.step_size:.1f} every {self.hold_time:.0f} min"
        painter.drawText(info_rect, Qt.AlignRight | Qt.AlignVCenter, info_text)


class BathWorker(QThread):
    update_status = pyqtSignal(str)
    update_gauge = pyqtSignal(float, float)
    update_temps = pyqtSignal(float, float)  # For separate temperature display widgets
    comm_status = pyqtSignal(str, bool)
    run_state_changed = pyqtSignal(str)
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
                self.run_state_changed.emit("Running")
                self.update_gauge.emit(status_display, target_display)
            elif status == "dose":
                self.run_state_changed.emit("Waiting for Dose")
                # Show dose prompt dialog with countdown timer
                dialog = DosePromptDialog(self.parent_widget, timeout_seconds=900)
                result = dialog.exec_()
                if not dialog.is_confirmed():
                    # Timeout occurred - pause the run
                    self.update_status.emit("Dose timeout - pausing run. Click Resume to continue.")
                    self.run_state_changed.emit("Paused")
                    self.pause()
                    while self.check_pressure_pause():
                        time.sleep(0.5)
                else:
                    self.update_status.emit("Dosed. Continuing...")
                    self.run_state_changed.emit("Running")
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
                        self.run_state_changed.emit("Running")
                        self.update_gauge.emit(status_display, target_display)
                    elif status == "dose":
                        self.run_state_changed.emit("Waiting for Dose")
                        # Show dose prompt dialog with countdown timer
                        dialog = DosePromptDialog(self.parent_widget, timeout_seconds=900)
                        result = dialog.exec_()
                        if not dialog.is_confirmed():
                            # Timeout occurred - pause the run
                            self.update_status.emit("Dose timeout - pausing run. Click Resume to continue.")
                            self.run_state_changed.emit("Paused")
                            self.pause()
                            while self.check_pressure_pause():
                                time.sleep(0.5)
                        else:
                            self.update_status.emit("Dosed. Continuing...")
                            self.run_state_changed.emit("Running")
                    elif status == "pause_on_pressure":
                        self.update_status.emit("Paused due to over-pressure. Click Resume to continue.")
                        self.run_state_changed.emit("Over-pressure Hold")
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
            elif self.routine == "Smart Dynamic":
                def smart_dynamic_callback(status=None):
                    if self.check_pause_stop():
                        raise Exception("Stopped")
                    nonlocal target_c
                    if isinstance(status, (float, int)):
                        status_display = self._to_display_temp(status)
                        target_display = self._to_display_temp(target_c)
                        self.update_status.emit(f"Current temp: {status_display:.2f} °{self.temp_unit}")
                        self.run_state_changed.emit("Running")
                        self.update_gauge.emit(status_display, target_display)
                    elif status == "dose":
                        self.run_state_changed.emit("Waiting for Dose")
                        dialog = DosePromptDialog(self.parent_widget, timeout_seconds=900)
                        result = dialog.exec_()
                        if not dialog.is_confirmed():
                            self.update_status.emit("Dose timeout - pausing run. Click Resume to continue.")
                            self.run_state_changed.emit("Paused")
                            self.pause()
                            while self.check_pressure_pause():
                                time.sleep(0.5)
                        else:
                            self.update_status.emit("Dosed. Continuing...")
                            self.run_state_changed.emit("Running")
                    elif status == "pause_on_pressure":
                        self.update_status.emit("Paused due to dP/dt threshold. Click Resume to continue.")
                        self.run_state_changed.emit("dP/dt Plateau Hold")
                        self.pause()
                        while self.check_pressure_pause():
                            time.sleep(0.5)
                    elif isinstance(status, dict) and 'target' in status:
                        target_c = status['target']
                        self._active_target_c = target_c
                        if status.get('plateau'):
                            self.run_state_changed.emit("dP/dt Plateau Hold")

                thermobath_core.smart_dynamic_profile(
                    bath,
                    cloud_point=self.params['cloud_point'],
                    min_temp=self.params['min_temp'],
                    initial_overheat=self.params['initial_overheat'],
                    callback=smart_dynamic_callback,
                    pressure_reader=self.pressure_reader,
                    pressure_channel=self.pressure_channels,
                    dp_dt_threshold=self.params['dp_dt_threshold'],
                    monitor_window=self.params['monitor_window'],
                    pressure_check_interval=self.pressure_check_interval,
                )
                self.update_status.emit("Smart Dynamic profile complete.")
                min_display = self._to_display_temp(self.params['min_temp'])
                self.update_gauge.emit(min_display, min_display)
        except Exception as e:
            if str(e) == "Stopped":
                self.update_status.emit("Stopped by user.")
                self.run_state_changed.emit("Stopped")
            else:
                self.update_status.emit(f"Routine failed: {e}")
                self.run_state_changed.emit("Error")
                self.comm_status.emit(f"Communication error: {e}", False)
        bath.close()
        self._bath = None
        self.finished.emit()

class ThermobathUI(QWidget):
    def __init__(self):
        super().__init__()
        self.engineering_config = self._default_engineering_config()
        # Initialize pressure-related attributes early to avoid AttributeError in signal handlers
        self._last_pressure_values = []
        self._last_pressure_update_time = None
        self.setWindowTitle("Thermobath Controller")
        self.resize(480, 750)  # Set a reasonable default size
        self.setMinimumSize(450, 700)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(dark_stylesheet())

        # Main split layout: sidebar on the left, stacked pages on the right.
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.setLayout(main_layout)

        self.sidebar_widget = QFrame()
        self.sidebar_widget.setObjectName("sidebarPanel")
        self.sidebar_widget.setFixedWidth(250)
        self.sidebar_widget.setFrameShape(QFrame.NoFrame)
        sidebar_layout = QVBoxLayout(self.sidebar_widget)
        sidebar_layout.setContentsMargins(18, 22, 18, 22)
        sidebar_layout.setSpacing(12)

        sidebar_brand = QWidget()
        sidebar_brand_layout = QVBoxLayout(sidebar_brand)
        sidebar_brand_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_brand_layout.setSpacing(2)
        sidebar_title = QLabel("THERMOBATH")
        sidebar_title.setObjectName("sidebarTitle")
        sidebar_subtitle = QLabel("Control deck and data supervision")
        sidebar_subtitle.setObjectName("sidebarSubtitle")
        sidebar_brand_layout.addWidget(sidebar_title)
        sidebar_brand_layout.addWidget(sidebar_subtitle)
        sidebar_layout.addWidget(sidebar_brand)

        self.sidebar_buttons = []
        for index, label in enumerate([
            "Dashboard",
            "Method Editor",
            "Hardware",
            "Data Logging",
        ]):
            button = QPushButton(label)
            button.setObjectName("sidebarBtn")
            button.setCheckable(True)
            button.setMinimumHeight(56)
            button.clicked.connect(lambda checked, page_index=index: self.set_active_page(page_index))
            sidebar_layout.addWidget(button)
            self.sidebar_buttons.append(button)
        sidebar_layout.addStretch(1)
        main_layout.addWidget(self.sidebar_widget)

        self.page_stack = QStackedWidget()
        main_layout.addWidget(self.page_stack, 1)

        self.dashboard_page = QWidget()
        self.dashboard_layout = QVBoxLayout(self.dashboard_page)
        self.dashboard_layout.setSpacing(18)
        self.dashboard_layout.setContentsMargins(22, 22, 22, 22)
        self.dashboard_layout.addWidget(
            self._create_page_header(
                "Dashboard",
                "Live temperature control, loop pressure telemetry, and routine state.",
            )
        )

        self.method_editor_page = QWidget()
        self.method_editor_layout = QVBoxLayout(self.method_editor_page)
        self.method_editor_layout.setSpacing(18)
        self.method_editor_layout.setContentsMargins(22, 22, 22, 22)
        self.method_editor_layout.addWidget(
            self._create_page_header(
                "Method Editor",
                "Build constant, stepwise, and smart dynamic temperature routines with live preview.",
            )
        )

        self.method_editor_content = QHBoxLayout()
        self.method_editor_content.setSpacing(18)
        self.method_form_layout = QVBoxLayout()
        self.method_form_layout.setSpacing(12)
        self.method_editor_content.addLayout(self.method_form_layout, 3)

        self.profile_preview = ProfilePreviewWidget()
        self.method_editor_content.addWidget(self.profile_preview, 2)
        self.method_editor_layout.addLayout(self.method_editor_content, 1)

        self.hardware_page = QWidget()
        self.hardware_layout = QVBoxLayout(self.hardware_page)
        self.hardware_layout.setSpacing(18)
        self.hardware_layout.setContentsMargins(22, 22, 22, 22)
        self.hardware_layout.addWidget(
            self._create_page_header(
                "Hardware",
                "Serial transport, DAQ connectivity, and safety interlocks for the bath and sensors.",
            )
        )

        self.data_logging_page = QWidget()
        self.data_logging_layout = QVBoxLayout(self.data_logging_page)
        self.data_logging_layout.setSpacing(18)
        self.data_logging_layout.setContentsMargins(22, 22, 22, 22)
        self.data_logging_layout.addWidget(
            self._create_page_header(
                "Data Logging",
                "Pressure monitoring, CSV capture, and plot scaling for runtime analysis.",
            )
        )

        self.page_stack.addWidget(self.dashboard_page)
        self.page_stack.addWidget(self.method_editor_page)
        self.page_stack.addWidget(self.hardware_page)
        self.page_stack.addWidget(self.data_logging_page)

        self.page_stack.currentChanged.connect(self.update_sidebar_state)
        self.set_active_page(0)

        # Dashboard: live operations view.
        self.state_tracker_states = ["Idle", "Ramping", "Dosing", "Cooling", "Plateau"]
        self.state_tracker_labels = {}
        dashboard_header_strip = QWidget()
        dashboard_header_strip.setObjectName("glassPanel")
        dashboard_header_layout = QVBoxLayout(dashboard_header_strip)
        dashboard_header_layout.setContentsMargins(18, 16, 18, 16)
        dashboard_header_layout.setSpacing(10)

        state_tracker_row = QHBoxLayout()
        state_tracker_row.setSpacing(6)
        for i, state_name in enumerate(self.state_tracker_states):
            state_label = QLabel(state_name)
            state_label.setStyleSheet("color: #6b7280; font-weight: bold;")
            self.state_tracker_labels[state_name] = state_label
            state_tracker_row.addWidget(state_label)
            if i < len(self.state_tracker_states) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("color: #4b5563;")
                state_tracker_row.addWidget(arrow)
        state_tracker_row.addStretch()
        dashboard_header_layout.addLayout(state_tracker_row)

        # Temperature display at top
        temp_display_layout = QHBoxLayout()
        self.current_temp_label = QLabel("Current: 77.00°F")
        self.current_temp_label.setObjectName("telemetryReadout")
        self.current_temp_label.setStyleSheet("color: #8ec07c;")
        self.target_temp_label = QLabel("Target: 77.00°F")
        self.target_temp_label.setObjectName("telemetryReadout")
        self.target_temp_label.setStyleSheet("color: #83a598;")
        temp_display_layout.addWidget(self.current_temp_label)
        temp_display_layout.addStretch()
        temp_display_layout.addWidget(self.target_temp_label)
        dashboard_header_layout.addLayout(temp_display_layout)
        self.dashboard_layout.addWidget(dashboard_header_strip)

        # Thermostat gauge
        self.gauge = ThermostatGauge()
        self.dashboard_layout.addWidget(self.gauge, alignment=Qt.AlignCenter)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self.kpi_cards = []
        for index in range(4):
            card = KPICard(f"Loop {index + 1}")
            self.kpi_cards.append(card)
            kpi_row.addWidget(card, 1)
        self.dashboard_layout.addLayout(kpi_row)
        self._refresh_kpi_titles()

        self.pressure_plot = PressurePlot(channels=4)
        self.pressure_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.dashboard_layout.addWidget(self.pressure_plot, 1)

        dashboard_bottom = QWidget()
        dashboard_bottom.setObjectName("glassPanel")
        dashboard_bottom_layout = QVBoxLayout(dashboard_bottom)
        dashboard_bottom_layout.setContentsMargins(18, 16, 18, 16)
        dashboard_bottom_layout.setSpacing(10)

        self.status_label = QLabel("Status: Ready")
        self.status_label.setObjectName("statusPill")
        dashboard_bottom_layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.start_button = QPushButton("Start Routine")
        self.start_button.setObjectName("startBtn")
        self.start_button.setIcon(QIcon.fromTheme("media-playback-start"))
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("pauseBtn")
        self.pause_button.setIcon(QIcon.fromTheme("media-playback-pause"))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopBtn")
        self.stop_button.setIcon(QIcon.fromTheme("process-stop"))
        self.start_button.setMinimumHeight(50)
        self.pause_button.setMinimumHeight(50)
        self.stop_button.setMinimumHeight(50)
        btn_row.addWidget(self.start_button)
        btn_row.addWidget(self.pause_button)
        btn_row.addWidget(self.stop_button)
        dashboard_bottom_layout.addLayout(btn_row)

        self.dashboard_layout.addWidget(dashboard_bottom)

        self.method_group = QGroupBox("Method Editor")
        method_group_layout = QVBoxLayout(self.method_group)
        method_group_layout.setContentsMargins(14, 20, 14, 14)
        method_group_layout.setSpacing(10)

        self.routine_box = QComboBox()
        self.routine_box.clear()
        self.routine_box.addItem(QIcon.fromTheme("media-playback-start"), "Constant")
        self.routine_box.addItem(QIcon.fromTheme("view-refresh"), "Stepwise")
        self.routine_box.addItem(QIcon.fromTheme("view-statistics"), "Smart Dynamic")
        self.routine_row = labeled_icon_row("Routine:", "view-list-text", self.routine_box)
        method_group_layout.addLayout(self.routine_row)
        self.method_form_layout.addWidget(self.method_group)

        # Constant temp widgets
        self.constant_temp_input = QDoubleSpinBox()
        self.constant_temp_input.setRange(-58, 302)
        self.constant_temp_input.setSuffix(" °F")
        self.constant_temp_input.setValue(77.0)
        self.constant_temp_row = labeled_icon_row("Setpoint Temperature:", "temperature", self.constant_temp_input)
        method_group_layout.addLayout(self.constant_temp_row)

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

        self.cloud_point_row = labeled_icon_row("Cloud Point:", "cloud", self.cloud_point_input)
        self.step_size_row = labeled_icon_row("Step Size:", "arrow-down", self.step_size_input)
        self.hold_time_row = labeled_icon_row("Hold Time (min):", "clock", self.hold_time_input)
        self.min_temp_row = labeled_icon_row("Minimum Temperature:", "thermometer", self.min_temp_input)
        self.initial_overheat_row = labeled_icon_row("Initial Overheat:", "fire", self.initial_overheat_input)
        method_group_layout.addLayout(self.cloud_point_row)
        method_group_layout.addLayout(self.step_size_row)
        method_group_layout.addLayout(self.hold_time_row)
        method_group_layout.addLayout(self.min_temp_row)
        method_group_layout.addLayout(self.initial_overheat_row)

        self.dp_dt_threshold_input = QDoubleSpinBox()
        self.dp_dt_threshold_input.setRange(0.0, 1e6)
        self.dp_dt_threshold_input.setDecimals(6)
        self.dp_dt_threshold_input.setSingleStep(0.001)
        self.dp_dt_threshold_input.setValue(0.01)
        self.dp_dt_threshold_input.setSuffix(" /s")
        self.dp_dt_threshold_row = labeled_icon_row("dP/dt Threshold:", "view-statistics", self.dp_dt_threshold_input)
        method_group_layout.addLayout(self.dp_dt_threshold_row)

        self.monitor_window_input = QSpinBox()
        self.monitor_window_input.setRange(1, 3600)
        self.monitor_window_input.setValue(30)
        self.monitor_window_input.setSuffix(" s")
        self.monitor_window_row = labeled_icon_row("Rate Monitor Window:", "clock", self.monitor_window_input)
        method_group_layout.addLayout(self.monitor_window_row)

        # Hardware page configuration groups.
        hardware_ports_group = QGroupBox("Port Configuration")
        hardware_ports_layout = QVBoxLayout(hardware_ports_group)

        ribbon_row = QHBoxLayout()
        ribbon_row.addWidget(QLabel("Tools:"))
        self.command_help_button = QPushButton("USB Commands")
        self.engineering_button = QPushButton("Engineering Settings")
        ribbon_row.addWidget(self.command_help_button)
        ribbon_row.addWidget(self.engineering_button)
        ribbon_row.addStretch()
        hardware_ports_layout.addLayout(ribbon_row)

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
        hardware_ports_layout.addLayout(comm_layout)

        self.transport_label = QLabel("Transport: USB serial (COM)")
        self.transport_label.setObjectName("sectionNote")
        hardware_ports_layout.addWidget(self.transport_label)

        self.hardware_layout.addWidget(hardware_ports_group)

        hardware_daq_group = QGroupBox("DAQ Hardware")
        hardware_daq_layout = QVBoxLayout(hardware_daq_group)

        # DATAQ DAQ widgets
        self.daq_com_port_combo = QComboBox()
        self.refresh_daq_ports_button = QPushButton("Refresh DAQ Ports")

        daq_port_row = QHBoxLayout()
        daq_port_row.addWidget(QLabel("DAQ COM Port:"))
        daq_port_row.addWidget(self.daq_com_port_combo)
        daq_port_row.addWidget(self.refresh_daq_ports_button)
        hardware_daq_layout.addLayout(daq_port_row)

        self.hardware_layout.addWidget(hardware_daq_group)

        hardware_safety_group = QGroupBox("Safety")
        hardware_safety_layout = QVBoxLayout(hardware_safety_group)

        self.sample_rate_hz_input = QDoubleSpinBox()
        self.sample_rate_hz_input.setRange(0.1, 20.0)
        self.sample_rate_hz_input.setDecimals(2)
        self.sample_rate_hz_input.setSingleStep(0.1)
        self.sample_rate_hz_input.setValue(1.0)
        self.sample_rate_hz_input.setSuffix(" Hz")

        self.pressure_max_input = QDoubleSpinBox()
        self.pressure_max_input.setRange(0, 1e6)
        self.pressure_max_input.setDecimals(3)
        self.pressure_max_input.setValue(1.0)
        hardware_safety_layout.addLayout(
            labeled_icon_row("Pressure max (unit):", "dialog-warning", self.pressure_max_input)
        )

        self.over_pressure_behavior = QComboBox()
        self.over_pressure_behavior.addItems(["Extend Hold", "Pause", "Abort"])
        hardware_safety_layout.addLayout(
            labeled_icon_row("Over-pressure behavior:", "process-stop", self.over_pressure_behavior)
        )

        self.hardware_layout.addWidget(hardware_safety_group)
        self.hardware_layout.addStretch(1)

        # Data logging page configuration groups.
        data_logging_group = QGroupBox("Data Logging")
        data_logging_group_layout = QVBoxLayout(data_logging_group)

        self.start_monitor_button = QPushButton("Start Pressure Monitor")
        self.stop_monitor_button = QPushButton("Stop Pressure Monitor")
        self.stop_monitor_button.setEnabled(False)
        monitor_row = QHBoxLayout()
        monitor_row.addWidget(self.start_monitor_button)
        monitor_row.addWidget(self.stop_monitor_button)
        data_logging_group_layout.addLayout(monitor_row)

        # Pressure monitor controls (replaces the table)
        self.view_pressure_button = QPushButton("View Pressure Data")
        self.view_pressure_button.setIcon(QIcon.fromTheme("view-list-details"))
        data_logging_group_layout.addWidget(self.view_pressure_button)

        log_file_row = QHBoxLayout()
        log_file_row.addWidget(QLabel("Log output file:"))
        self.log_file_input = QLineEdit()
        self.log_file_input.setPlaceholderText("Browse or type a path to create a new CSV...")
        self.log_browse_button = QPushButton("Browse...")
        log_file_row.addWidget(self.log_file_input)
        log_file_row.addWidget(self.log_browse_button)
        data_logging_group_layout.addLayout(log_file_row)

        log_ctrl_row = QHBoxLayout()
        self.start_log_button = QPushButton("Start Logging")
        self.stop_log_button = QPushButton("Stop Logging")
        self.stop_log_button.setEnabled(False)
        self.log_rows_label = QLabel("Not logging")
        self.log_rows_label.setObjectName("sectionNote")
        log_ctrl_row.addWidget(self.start_log_button)
        log_ctrl_row.addWidget(self.stop_log_button)
        log_ctrl_row.addWidget(self.log_rows_label)
        data_logging_group_layout.addLayout(log_ctrl_row)

        data_logging_group_layout.addLayout(
            labeled_icon_row("Logger sample rate:", "view-refresh", self.sample_rate_hz_input)
        )

        channel_layout = QVBoxLayout()
        channel_layout.addWidget(QLabel("Pressure channels (0-based indices for DAQ):"))
        self.channel_inputs = []
        grid = QGridLayout()
        for i in range(4):
            grid.addWidget(QLabel(f"Ch {i+1}:"), i, 0)
            self.channel_inputs.append(QLineEdit(str(i)))
            grid.addWidget(self.channel_inputs[i], i, 1)
        channel_layout.addLayout(grid)
        data_logging_group_layout.addLayout(channel_layout)

        self.data_logging_layout.addWidget(data_logging_group)

        self.log_browse_button.clicked.connect(self.browse_log_output)
        self.start_log_button.clicked.connect(self.start_logging)
        self.stop_log_button.clicked.connect(self.stop_logging)

        plot_scale_group = QGroupBox("Plot Scale")
        plot_scale_layout = QVBoxLayout(plot_scale_group)

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
        plot_scale_layout.addLayout(scale_row)

        self.reset_scale_button = QPushButton("Reset Scale")
        plot_scale_layout.addWidget(self.reset_scale_button)
        self.data_logging_layout.addWidget(plot_scale_group)
        self.data_logging_layout.addStretch(1)

        # Config save/load
        self.save_config_button = QPushButton("Save Config")
        self.load_config_button = QPushButton("Load Config")
        config_footer = QWidget()
        config_footer.setObjectName("footerBar")
        config_row = QHBoxLayout()
        config_row.setContentsMargins(16, 14, 16, 14)
        config_row.setSpacing(10)
        config_row.addWidget(self.save_config_button)
        config_row.addWidget(self.load_config_button)
        self.method_form_layout.addStretch(1)
        config_footer.setLayout(config_row)
        self.method_editor_layout.addWidget(config_footer)

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

        self.start_button.clicked.connect(self.start_routine)
        self.pause_button.clicked.connect(self.pause_resume_routine)
        self.stop_button.clicked.connect(self.stop_routine)
        self.temp_unit_combo.currentTextChanged.connect(self.on_temp_unit_changed)
        self.routine_box.currentIndexChanged.connect(self.update_fields)
        self.routine_box.currentIndexChanged.connect(self.update_profile_preview)
        self.constant_temp_input.valueChanged.connect(self.update_profile_preview)
        self.cloud_point_input.valueChanged.connect(self.update_profile_preview)
        self.step_size_input.valueChanged.connect(self.update_profile_preview)
        self.hold_time_input.valueChanged.connect(self.update_profile_preview)
        self.min_temp_input.valueChanged.connect(self.update_profile_preview)
        self.initial_overheat_input.valueChanged.connect(self.update_profile_preview)
        self.dp_dt_threshold_input.valueChanged.connect(self.update_profile_preview)
        self.monitor_window_input.valueChanged.connect(self.update_profile_preview)
        self.pressure_max_input.valueChanged.connect(self.refresh_kpi_cards)
        self.temp_unit = "F"
        self.apply_temp_unit("F", convert_values=False)
        self.update_fields()
        self.update_profile_preview()
        self.load_config()
        self.update_profile_preview()
        self.update_plot_scale()
        self.worker = None
        self.monitor_thread = None
        self.paused = False
        self.pressure_dialog = None
        self._data_logger = None
        self._log_timer = None
        self._last_temp = None
        self._current_run_state = "Idle"
        self.set_run_state("Idle")

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

    def set_active_page(self, index):
        self.page_stack.setCurrentIndex(index)
        self.update_sidebar_state(index)

    def update_sidebar_state(self, index):
        for button_index, button in enumerate(self.sidebar_buttons):
            button.setChecked(button_index == index)

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

    def _create_page_header(self, title, subtitle):
        header = QWidget()
        header.setObjectName("pageHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        return header

    def _refresh_kpi_titles(self):
        if not hasattr(self, "kpi_cards"):
            return

        for index, card in enumerate(self.kpi_cards):
            cfg = self.engineering_config[index] if index < len(self.engineering_config) else {}
            title = cfg.get("label") or f"Loop {index + 1}"
            card.set_title(title)

    def _update_kpi_cards(self, values):
        now = time.time()
        dt = None if self._last_pressure_update_time is None else max(0.0, now - self._last_pressure_update_time)
        threshold = self.pressure_max_input.value() if hasattr(self, "pressure_max_input") else None

        for index, card in enumerate(self.kpi_cards):
            value = values[index] if index < len(values) else None
            previous_value = self._last_pressure_values[index] if index < len(self._last_pressure_values) else None
            cfg = self.engineering_config[index] if index < len(self.engineering_config) else {}
            unit = cfg.get("eu_label", "EU")

            rate = None
            if dt and dt > 0 and value is not None and previous_value is not None:
                rate = (value - previous_value) / dt

            card.update_metrics(value, unit=unit, rate=rate, threshold=threshold)

        self._last_pressure_update_time = now

    def refresh_kpi_cards(self, *_args):
        if hasattr(self, '_last_pressure_values'):
            self._update_kpi_cards(self._last_pressure_values)

    def show_command_reference(self):
        dialog = CommandReferenceDialog(self)
        dialog.exec_()

    def open_engineering_settings(self):
        dialog = EngineeringSettingsDialog(self.engineering_config, self)
        if dialog.exec_() == QDialog.Accepted:
            self.engineering_config = dialog.get_settings()
            self._refresh_kpi_titles()
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
        headers.append("Run State")
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
        row = [ts, temp_str] + pressure_strs[:4] + [self._current_run_state]
        try:
            self._data_logger.write_row(row)
            self.log_rows_label.setText(f"Logging — {self._data_logger.row_count} rows")
        except Exception as e:
            self.log_rows_label.setText(f"Log error: {e}")

    def set_run_state(self, state):
        normalized_state = str(state).strip() if state else "Idle"
        self._current_run_state = normalized_state

        lower_state = normalized_state.lower()
        if "dose" in lower_state:
            tracker_state = "Dosing"
        elif "plateau" in lower_state or "hold" in lower_state:
            tracker_state = "Plateau"
        elif lower_state == "running":
            tracker_state = "Ramping"
        elif lower_state == "paused":
            tracker_state = "Cooling"
        elif lower_state in {"stopped", "error", "idle"}:
            tracker_state = "Idle"
        else:
            tracker_state = "Idle"

        for state_name, label in self.state_tracker_labels.items():
            if state_name == tracker_state:
                label.setStyleSheet("color: #00dc78; font-weight: bold;")
            else:
                label.setStyleSheet("color: #6b7280; font-weight: bold;")

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
        self._update_kpi_cards(values)
        self._last_pressure_values = list(values)

    def update_fields(self):
        routine = self.routine_box.currentText()
        show_stepwise = routine == "Stepwise"
        show_smart_dynamic = routine == "Smart Dynamic"
        show_profile_fields = show_stepwise or show_smart_dynamic

        self._set_layout_widgets_visible(self.constant_temp_row, routine == "Constant")
        self._set_layout_widgets_visible(self.cloud_point_row, show_profile_fields)
        self._set_layout_widgets_visible(self.step_size_row, show_stepwise)
        self._set_layout_widgets_visible(self.hold_time_row, show_stepwise)
        self._set_layout_widgets_visible(self.min_temp_row, show_profile_fields)
        self._set_layout_widgets_visible(self.initial_overheat_row, show_profile_fields)
        self._set_layout_widgets_visible(self.dp_dt_threshold_row, show_smart_dynamic)
        self._set_layout_widgets_visible(self.monitor_window_row, show_smart_dynamic)

    def update_profile_preview(self, *_args):
        self.profile_preview.set_profile_params(
            self.routine_box.currentText(),
            self.constant_temp_input.value(),
            self.cloud_point_input.value(),
            self.step_size_input.value(),
            self.hold_time_input.value(),
            self.min_temp_input.value(),
            self.initial_overheat_input.value(),
            self.dp_dt_threshold_input.value(),
            self.monitor_window_input.value(),
        )

    def _set_layout_widgets_visible(self, layout, visible):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(visible)

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
        self.update_profile_preview()

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
        self.dp_dt_threshold_input.setValue(config.get("dp_dt_threshold", 0.01))
        self.monitor_window_input.setValue(config.get("monitor_window", 30))

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
            "dp_dt_threshold": self.dp_dt_threshold_input.value(),
            "monitor_window": self.monitor_window_input.value(),
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
        elif routine == "Stepwise":
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
        elif routine == "Smart Dynamic":
            cloud_display = self.cloud_point_input.value()
            min_display = self.min_temp_input.value()
            overheat_display = self.initial_overheat_input.value()
            factor = 5.0 / 9.0 if unit == "F" else 1.0
            params = {
                'cloud_point': f_to_c(cloud_display) if unit == "F" else cloud_display,
                'min_temp': f_to_c(min_display) if unit == "F" else min_display,
                'initial_overheat': overheat_display * factor,
                'dp_dt_threshold': self.dp_dt_threshold_input.value(),
                'monitor_window': self.monitor_window_input.value(),
            }
            self.gauge.set_temps(self.gauge.current_temp, cloud_display + overheat_display)
            self.update_temp_labels(self.gauge.current_temp, cloud_display + overheat_display)
        else:
            self.status_label.setText(f"Status: Unknown routine '{routine}'.")
            return
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
        self.worker.run_state_changed.connect(self.set_run_state)
        self.worker.update_gauge.connect(self.gauge.set_temps)
        self.worker.update_gauge.connect(self.update_temp_labels)
        self.worker.update_gauge.connect(self._update_last_temp)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()
        self.set_run_state(f"Running ({routine})")
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
        self.set_run_state("Idle")
        self.paused = False
        self.pause_button.setText("Pause")

        self.monitor_thread = None

def labeled_icon_row(label, icon_name, widget):
    row = QHBoxLayout()
    row.setSpacing(10)
    icon = QIcon.fromTheme(icon_name)
    icon_label = QLabel()
    icon_label.setFixedWidth(22)
    if not icon.isNull():
        icon_label.setPixmap(icon.pixmap(20, 20))
    label_widget = QLabel(label)
    label_widget.setMinimumWidth(150)
    row.addWidget(icon_label)
    row.addWidget(label_widget)
    row.addWidget(widget, 1)
    return row

def dark_stylesheet():
    return """
    QWidget {
        background-color: #121921;
        color: #e6edf3;
        font-family: Segoe UI, Arial, sans-serif;
        font-size: 14px;
    }
    QWidget#pageHeader {
        background: transparent;
        border: none;
    }
    QLabel#pageTitle {
        color: #f4fbff;
        font-size: 24px;
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    QLabel#pageSubtitle {
        color: #8da4b8;
        font-size: 13px;
    }
    QLabel#sidebarTitle {
        color: #f6fbff;
        font-size: 20px;
        font-weight: 800;
        letter-spacing: 1px;
    }
    QLabel#sidebarSubtitle {
        color: #8aa3b8;
        font-size: 12px;
    }
    QFrame#sidebarPanel {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #0f161d, stop:0.55 #17212c, stop:1 #0c131a);
        border-right: 1px solid #2b3947;
    }
    QWidget#glassPanel, QWidget#footerBar {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(20, 28, 37, 0.98), stop:1 rgba(13, 19, 26, 0.98));
        border: 1px solid #314252;
        border-radius: 14px;
    }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: rgba(25, 34, 44, 0.92);
        color: #e6edf3;
        border: 1px solid #3b4c5f;
        border-radius: 8px;
        padding: 6px 8px;
        min-height: 28px;
    }
    QPushButton {
        background-color: #24303c;
        color: #e6edf3;
        border: 1px solid #425468;
        border-radius: 8px;
        padding: 8px 14px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #2f3d4b;
        border-color: #5a738d;
    }
    QPushButton:pressed {
        background-color: #1f2a35;
    }
    QLabel {
        color: #e6edf3;
    }
    QLabel#telemetryReadout {
        font-size: 15px;
        font-weight: 700;
    }
    QLabel#statusPill {
        background-color: rgba(11, 58, 42, 0.58);
        border: 1px solid rgba(57, 166, 123, 0.55);
        border-radius: 10px;
        color: #b8f5d8;
        padding: 8px 12px;
    }
    QLabel#sectionNote {
        color: #87a5bc;
        font-size: 12px;
    }
    QComboBox QAbstractItemView {
        background-color: #17202a;
        color: #e6edf3;
        selection-background-color: #2a9d8f;
    }
    QGroupBox {
        border: 1px solid #33485c;
        border-radius: 14px;
        margin-top: 14px;
        padding-top: 12px;
        background-color: rgba(17, 24, 32, 0.9);
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 8px;
        color: #9dc3e0;
        font-weight: 700;
    }
    QPushButton#sidebarBtn {
        background-color: rgba(20, 29, 38, 0.9);
        color: #c9d8e6;
        border: 1px solid #2e3e4e;
        border-radius: 14px;
        padding: 16px 18px;
        text-align: left;
        font-size: 15px;
        font-weight: 700;
    }
    QPushButton#sidebarBtn:hover {
        background-color: rgba(34, 48, 61, 0.98);
        border-color: #4a6379;
        color: #f4fbff;
    }
    QPushButton#sidebarBtn:checked {
        background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #17324a, stop:1 #24557a);
        border: 1px solid #5ea6d6;
        color: #ffffff;
    }
    QPushButton#sidebarBtn:pressed {
        background-color: #1f435f;
    }
    QPushButton#startBtn {
        background-color: #0e6b4b;
        border: 1px solid #2aa678;
        border-radius: 12px;
        color: #f4fff9;
        font-weight: 800;
        font-size: 15px;
        padding: 12px 20px;
    }
    QPushButton#startBtn:hover {
        background-color: #14845d;
        border-color: #48c594;
    }
    QPushButton#pauseBtn {
        background-color: #8b4b11;
        border: 1px solid #d0852e;
        border-radius: 12px;
        color: #fff7ec;
        font-weight: 800;
        font-size: 15px;
        padding: 12px 20px;
    }
    QPushButton#pauseBtn:hover {
        background-color: #a75c18;
        border-color: #f0a94a;
    }
    QPushButton#stopBtn {
        background-color: #8b1e2d;
        border: 1px solid #d14a5f;
        border-radius: 12px;
        color: #fff4f6;
        font-weight: 800;
        font-size: 15px;
        padding: 12px 20px;
    }
    QPushButton#stopBtn:hover {
        background-color: #a8283a;
        border-color: #ee6f84;
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