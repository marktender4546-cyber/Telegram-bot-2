"""Sana-vaqt yordamchilari. Testlash uchun Telegram kutubxonasiga bog'liq emas."""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class DateInputError(ValueError):
    pass


def get_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise DateInputError(
            "Bu vaqt mintaqasi topilmadi. Masalan: Asia/Tashkent"
        ) from exc


def parse_due_text(text: str, timezone_name: str, now: datetime | None = None) -> datetime:
    """Matnni foydalanuvchi vaqt mintaqasidagi kelajak vaqtga aylantiradi.

    Qabul qilinadigan namunalar: 2026-11-20 18:30, 20.11.2026 18:30,
    18:30, ertaga 09:00, 15m, 2h, 1d.
    Natija UTC timezone-aware datetime bo'ladi.
    """
    raw = text.strip().lower()
    if not raw:
        raise DateInputError("Sana yoki vaqt kiritilmadi.")

    zone = get_zone(timezone_name)
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(zone)

    relative = re.fullmatch(r"(\d+)\s*(m|min|mins|minute|minutes|daq|daqiqa|h|hr|hour|hours|soat|d|day|days|kun)", raw)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if amount <= 0 or amount > 3650:
            raise DateInputError("Nisbiy vaqt 1 dan 3650 gacha bo'lishi kerak.")
        if unit in {"m", "min", "mins", "minute", "minutes", "daq", "daqiqa"}:
            due = now_utc + timedelta(minutes=amount)
        elif unit in {"h", "hr", "hour", "hours", "soat"}:
            due = now_utc + timedelta(hours=amount)
        else:
            due = now_utc + timedelta(days=amount)
        return due.replace(microsecond=0)

    candidate: datetime | None = None
    if raw.startswith("ertaga "):
        time_part = raw.split(maxsplit=1)[1]
        parsed_time = _parse_clock(time_part)
        candidate = (local_now + timedelta(days=1)).replace(
            hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0
        )
    elif raw.startswith("bugun "):
        parsed_time = _parse_clock(raw.split(maxsplit=1)[1])
        candidate = local_now.replace(
            hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0
        )
    elif re.fullmatch(r"\d{1,2}:\d{2}", raw):
        parsed_time = _parse_clock(raw)
        candidate = local_now.replace(
            hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0
        )
        if candidate <= local_now:
            candidate += timedelta(days=1)
    else:
        formats = (
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%dT%H:%M",
        )
        for fmt in formats:
            try:
                candidate = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if candidate is None:
            raise DateInputError(
                "Vaqt formati noto'g'ri. Masalan: 20.11.2026 18:30, "
                "2026-11-20 18:30, 18:30, ertaga 09:00 yoki 30m."
            )

    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=zone)
    due_utc = candidate.astimezone(timezone.utc).replace(microsecond=0)
    if due_utc <= now_utc.replace(microsecond=0):
        raise DateInputError("Bu vaqt o'tib ketgan. Kelajakdagi vaqtni kiriting.")
    if due_utc > now_utc + timedelta(days=3650):
        raise DateInputError("Eslatmani 10 yildan uzoq muddatga qo'yib bo'lmaydi.")
    return due_utc


def _parse_clock(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise DateInputError("Soat formati HH:MM bo'lishi kerak. Masalan: 09:30") from exc


def next_occurrence(
    current_due_utc: datetime,
    repeat_rule: str,
    timezone_name: str,
    now: datetime,
    anchor_day: int | None = None,
) -> datetime:
    """Takroriy eslatmaning hozirgi vaqtdan keyingi navbatdagi sanasini topadi."""
    zone = get_zone(timezone_name)
    current = current_due_utc.astimezone(zone)
    now_local = now.astimezone(zone)

    candidate = current
    # 10 yillik oraliqdan ortiq orqada qolgan takroriy eslatma uchun cheklov.
    for _ in range(4000):
        if repeat_rule == "daily":
            candidate += timedelta(days=1)
        elif repeat_rule == "weekly":
            candidate += timedelta(days=7)
        elif repeat_rule == "weekdays":
            candidate += timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        elif repeat_rule == "monthly":
            # 29/30/31-kun kabi sanalar qisqa oylarda vaqtincha qisqaradi,
            # keyingi mos oyda esa asl kuniga qaytadi.
            year = candidate.year + (candidate.month // 12)
            month = (candidate.month % 12) + 1
            wanted_day = anchor_day or candidate.day
            day = min(wanted_day, calendar.monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
        else:
            raise ValueError(f"Noma'lum takrorlash turi: {repeat_rule}")
        if candidate > now_local:
            return candidate.astimezone(timezone.utc).replace(microsecond=0)
    raise ValueError("Keyingi takrorlanish sanasini hisoblab bo'lmadi.")


def format_datetime(utc_value: str | datetime, timezone_name: str) -> str:
    value = datetime.fromisoformat(utc_value) if isinstance(utc_value, str) else utc_value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(get_zone(timezone_name))
    return local.strftime("%d.%m.%Y, %H:%M")
