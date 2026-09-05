"""Characterization tests for the captured ZTTIN01 schedule format."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "zentraly"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

zentraly_package = types.ModuleType("custom_components.zentraly")
zentraly_package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.zentraly", zentraly_package)

homeassistant = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
homeassistant.__path__ = []
homeassistant_util = types.ModuleType("homeassistant.util")
homeassistant_util.__path__ = []
sys.modules.setdefault("homeassistant.util", homeassistant_util)
homeassistant_dt = types.ModuleType("homeassistant.util.dt")
homeassistant_dt.now = lambda: datetime.now(timezone.utc)
sys.modules.setdefault("homeassistant.util.dt", homeassistant_dt)
homeassistant_util.dt = sys.modules["homeassistant.util.dt"]

schedule = _load_module("custom_components.zentraly.schedule", PACKAGE_PATH / "schedule.py")


# [2 entries] 06:00 at 20 C every day; 18:00 at 22 C on Monday.
TWO_ENTRY_SCHEDULE = "0218287f01482c4001"


class ScheduleDecoderTests(unittest.TestCase):
    """Expected values are derived directly from the captured four-byte layout."""

    def test_empty_schedule_is_valid_when_count_is_zero(self) -> None:
        self.assertEqual(
            {"raw": "00", "count": 0, "entries": [], "parse_error": None},
            schedule.decode_schedule("00"),
        )

    def test_decodes_literal_entries(self) -> None:
        decoded = schedule.decode_schedule(TWO_ENTRY_SCHEDULE)

        self.assertIsNone(decoded["parse_error"])
        self.assertEqual(2, decoded["count"])
        self.assertEqual(
            {
                "index": 0,
                "time": "06:00",
                "slot": 24,
                "minutes": 360,
                "temperature": 20.0,
                "temperature_x2": 40,
                "days": [
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ],
                "days_mask": 0x7F,
                "flag": 1,
            },
            decoded["entries"][0],
        )
        self.assertEqual("18:00", decoded["entries"][1]["time"])
        self.assertEqual(22.0, decoded["entries"][1]["temperature"])
        self.assertEqual(["monday"], decoded["entries"][1]["days"])

    def test_rejects_truncated_invalid_and_out_of_range_slots(self) -> None:
        cases = {
            "0118287f": "invalid_length_4_expected_5",
            "zz": "invalid_hex",
            "0160287f01": "invalid_slot_96",
        }
        for raw, error in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(error, schedule.decode_schedule(raw)["parse_error"])

    def test_current_and_next_entry_on_monday_afternoon(self) -> None:
        now = datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc)  # Monday.

        current = schedule.current_scheduled_entry(TWO_ENTRY_SCHEDULE, now)
        next_entry = schedule.next_scheduled_entry(TWO_ENTRY_SCHEDULE, now)

        self.assertEqual("06:00", current["time"])
        self.assertEqual(20.0, current["temperature"])
        self.assertEqual("18:00", next_entry["time"])
        self.assertEqual(60, next_entry["in_minutes"])
        self.assertEqual("2026-08-31T18:00:00+00:00", next_entry["datetime_iso"])

    def test_next_entry_rolls_to_the_following_day(self) -> None:
        now = datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc)  # Monday.

        current = schedule.current_scheduled_entry(TWO_ENTRY_SCHEDULE, now)
        next_entry = schedule.next_scheduled_entry(TWO_ENTRY_SCHEDULE, now)

        self.assertEqual("18:00", current["time"])
        self.assertEqual(22.0, current["temperature"])
        self.assertEqual("06:00", next_entry["time"])
        self.assertEqual(660, next_entry["in_minutes"])
        self.assertEqual("2026-09-01T06:00:00+00:00", next_entry["datetime_iso"])

    def test_current_entry_can_come_from_same_weekday_last_week(self) -> None:
        # One Monday entry at 05:00; at 04:00 the active value is last Monday's.
        raw = "01142a4001"
        now = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)

        current = schedule.current_scheduled_entry(raw, now)

        self.assertIsNotNone(current)
        self.assertEqual("05:00", current["time"])
        self.assertEqual(21.0, current["temperature"])


if __name__ == "__main__":
    unittest.main()
