"""Schedule helpers for Zentraly ZTTIN01 thermostats."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


DAY_BITS: tuple[tuple[int, str], ...] = (
    (0x40, "monday"),
    (0x20, "tuesday"),
    (0x10, "wednesday"),
    (0x08, "thursday"),
    (0x04, "friday"),
    (0x02, "saturday"),
    (0x01, "sunday"),
)

WEEKDAY_TO_BIT = {
    0: 0x40,
    1: 0x20,
    2: 0x10,
    3: 0x08,
    4: 0x04,
    5: 0x02,
    6: 0x01,
}

DAY_LABELS = {
    "monday": "Lun",
    "tuesday": "Mar",
    "wednesday": "Mie",
    "thursday": "Jue",
    "friday": "Vie",
    "saturday": "Sab",
    "sunday": "Dom",
}


def schedule_entry_summary(entry: dict[str, Any]) -> str:
    """Return a concise, human-readable description of a schedule entry."""
    days = entry.get("days") or []
    day_label = (
        "Todos"
        if len(days) == len(DAY_BITS)
        else ", ".join(DAY_LABELS.get(day, day) for day in days)
    )
    return f"{day_label} {entry['time']} -> {entry['temperature']:.1f} C"


def compact_schedule_summary(entries: list[dict[str, Any]], max_length: int = 255) -> str:
    """Return a state-safe compact schedule summary."""
    parts: list[str] = []
    for entry in entries:
        part = f"{entry['time']} {entry['temperature']:.1f} C"
        candidate = " > ".join((*parts, part))
        if len(candidate) > max_length - 5:
            return " > ".join(parts) + (" > ..." if parts else "...")
        parts.append(part)

    return " > ".join(parts)


def today_scheduled_entries(
    raw_schedule: Any,
    now: datetime | None = None,
) -> list[dict[str, Any]] | None:
    """Return schedule entries that apply on the current local day."""
    decoded = decode_schedule(raw_schedule)
    if decoded.get("parse_error"):
        return None

    now = now or dt_util.now()
    day_bit = WEEKDAY_TO_BIT[now.weekday()]
    return [
        entry
        for entry in decoded["entries"]
        if entry["days_mask"] & day_bit
    ]


def decode_schedule(raw_schedule: Any) -> dict[str, Any]:
    """Decode a Zentraly schedule hex blob."""
    if raw_schedule is None:
        return {
            "raw": None,
            "count": None,
            "entries": [],
            "parse_error": "missing_schedule",
        }

    raw = str(raw_schedule).strip()
    if not raw:
        return {
            "raw": raw,
            "count": None,
            "entries": [],
            "parse_error": "missing_schedule",
        }

    if len(raw) % 2:
        return {
            "raw": raw,
            "count": None,
            "entries": [],
            "parse_error": "odd_hex_length",
        }

    try:
        data = bytes.fromhex(raw)
    except ValueError:
        return {
            "raw": raw,
            "count": None,
            "entries": [],
            "parse_error": "invalid_hex",
        }

    if not data:
        return {
            "raw": raw,
            "count": None,
            "entries": [],
            "parse_error": "empty_schedule",
        }

    count = data[0]
    expected_length = 1 + count * 4
    if len(data) != expected_length:
        return {
            "raw": raw,
            "count": count,
            "entries": [],
            "parse_error": f"invalid_length_{len(data)}_expected_{expected_length}",
        }

    entries = []
    for index in range(count):
        offset = 1 + index * 4
        slot = data[offset]
        temperature_x2 = data[offset + 1]
        days_mask = data[offset + 2]
        flag = data[offset + 3]
        minutes = slot * 15

        if slot > 95:
            return {
                "raw": raw,
                "count": count,
                "entries": [],
                "parse_error": f"invalid_slot_{slot}",
            }

        entries.append(
            {
                "index": index,
                "time": f"{minutes // 60:02d}:{minutes % 60:02d}",
                "slot": slot,
                "minutes": minutes,
                "temperature": temperature_x2 / 2,
                "temperature_x2": temperature_x2,
                "days": [
                    day_name
                    for bit, day_name in DAY_BITS
                    if days_mask & bit
                ],
                "days_mask": days_mask,
                "flag": flag,
            }
        )

    return {
        "raw": raw,
        "count": count,
        "entries": entries,
        "parse_error": None,
    }


def current_scheduled_entry(raw_schedule: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """Return the schedule entry active at the given local time."""
    decoded = decode_schedule(raw_schedule)
    entries = decoded.get("entries") or []
    if decoded.get("parse_error") or not entries:
        return None

    now = now or dt_util.now()

    best_entry: dict[str, Any] | None = None
    best_datetime: datetime | None = None

    for day_offset in range(8):
        candidate_date = (now - timedelta(days=day_offset)).date()
        weekday = (now.weekday() - day_offset) % 7
        day_bit = WEEKDAY_TO_BIT[weekday]

        for entry in entries:
            if not entry["days_mask"] & day_bit:
                continue
            entry_datetime = datetime.combine(
                candidate_date,
                datetime.min.time(),
                tzinfo=now.tzinfo,
            ) + timedelta(minutes=entry["minutes"])
            if entry_datetime > now:
                continue
            if best_datetime is None or entry_datetime > best_datetime:
                best_entry = entry
                best_datetime = entry_datetime

    return dict(best_entry) if best_entry is not None else None


def next_scheduled_entry(raw_schedule: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """Return the next future schedule entry within the next seven days."""
    decoded = decode_schedule(raw_schedule)
    entries = decoded.get("entries") or []
    if decoded.get("parse_error") or not entries:
        return None

    now = now or dt_util.now()
    current_minutes = now.hour * 60 + now.minute

    best_entry: dict[str, Any] | None = None
    best_delta: int | None = None
    best_datetime: datetime | None = None

    for day_offset in range(8):
        candidate_date = (now + timedelta(days=day_offset)).date()
        weekday = (now.weekday() + day_offset) % 7
        day_bit = WEEKDAY_TO_BIT[weekday]

        for entry in entries:
            if not entry["days_mask"] & day_bit:
                continue
            if day_offset == 0 and entry["minutes"] <= current_minutes:
                continue

            delta = day_offset * 24 * 60 + entry["minutes"] - current_minutes
            if delta <= 0 or delta > 7 * 24 * 60:
                continue
            if best_delta is not None and delta >= best_delta:
                continue

            entry_datetime = datetime.combine(
                candidate_date,
                datetime.min.time(),
                tzinfo=now.tzinfo,
            ) + timedelta(minutes=entry["minutes"])
            best_entry = entry
            best_delta = delta
            best_datetime = entry_datetime

    if best_entry is None or best_delta is None or best_datetime is None:
        return None

    result = dict(best_entry)
    result["datetime"] = best_datetime
    result["datetime_iso"] = best_datetime.isoformat()
    result["in_minutes"] = best_delta
    return result
