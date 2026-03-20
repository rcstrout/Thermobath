import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from thermobath import core


class _LegacyReader:
    def __init__(self, value):
        self.value = value

    def get_channel(self, channel):
        return self.value


class _SequencePressureSource(core.PressureSource):
    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def read_channel(self, channel):
        if self.index >= len(self.values):
            return self.values[-1]
        value = self.values[self.index]
        self.index += 1
        return value


class PressureSourceTests(unittest.TestCase):
    def test_windaq_pressure_source_reads_index_and_header(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "pressure.csv"
            path.write_text("P1,P2\n1.10,2.20\n3.30,4.40\n", encoding="utf-8")

            source = core.WinDAQPressureSource(path, delimiter=",", has_header=True)
            self.assertAlmostEqual(source.read_channel(0), 3.30, places=3)
            self.assertAlmostEqual(source.read_channel("P2"), 4.40, places=3)

    def test_windaq_pressure_source_returns_none_on_bad_value(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "pressure.csv"
            path.write_text("P1,P2\n1.10,ok\n", encoding="utf-8")

            source = core.WinDAQPressureSource(path, delimiter=",", has_header=True)
            self.assertIsNone(source.read_channel("P2"))

    def test_ensure_pressure_source_wraps_legacy_reader(self):
        wrapped = core.ensure_pressure_source(_LegacyReader(12.5))
        self.assertAlmostEqual(wrapped.read_channel(0), 12.5, places=3)

    def test_hold_temperature_extends_when_over_pressure(self):
        # hold_time_min=0.02 → 1.2 real seconds; check_interval=0.1s keeps the test fast.
        source = _SequencePressureSource([25.0, 10.0, 10.0])
        start = time.time()
        core.hold_temperature(
            bath=object(),
            hold_time_min=0.02,
            callback=None,
            pressure_reader=source,
            pressure_channel=0,
            pressure_max=20.0,
            check_interval=0.1,
            over_pressure_behavior="Extend Hold",
        )
        elapsed = time.time() - start

        # Base hold is ~1.2s; one over-pressure extension adds at least one check interval.
        self.assertGreaterEqual(elapsed, 1.2)

    def test_engineering_pressure_source_scales_channel(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "pressure.csv"
            path.write_text("V1,V2\n1.00,4.00\n", encoding="utf-8")

            raw_source = core.WinDAQPressureSource(path, delimiter=",", has_header=True)
            scaled_source = core.ChannelEngineeringPressureSource(
                raw_source,
                {
                    "V1": {
                        "volt_low": 0.0,
                        "volt_high": 5.0,
                        "eng_low": 0.0,
                        "eng_high": 500.0,
                    }
                },
            )

            self.assertAlmostEqual(scaled_source.read_channel("V1"), 100.0, places=3)
            # Channels without mapping are passed through unchanged.
            self.assertAlmostEqual(scaled_source.read_channel("V2"), 4.0, places=3)


if __name__ == "__main__":
    unittest.main()
