"""SQLite ma'lumotlar bazasi qatlami. Tashqi servis yoki pullik API talab qilmaydi."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterator

import config

DB_PATH = Path(config.DATABASE_FILE).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = Path(__file__).resolve().parent / DB_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                timezone TEXT NOT NULL DEFAULT 'Asia/Tashkent',
                joined_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                due_at TEXT NOT NULL,
                timezone TEXT NOT NULL,
                repeat_rule TEXT NOT NULL DEFAULT 'none',
                category TEXT NOT NULL DEFAULT 'other',
                priority TEXT NOT NULL DEFAULT 'normal',
                anchor_day INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                notified_at TEXT,
                snooze_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        # Eski bazalar uchun xavfsiz sxema yangilanishi.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()}
        if "anchor_day" not in columns:
            conn.execute("ALTER TABLE reminders ADD COLUMN anchor_day INTEGER")
        legacy_rows = conn.execute(
            "SELECT id, due_at, timezone FROM reminders WHERE anchor_day IS NULL"
        ).fetchall()
        for legacy_row in legacy_rows:
            try:
                local_day = datetime.fromisoformat(legacy_row["due_at"]).astimezone(
                    ZoneInfo(legacy_row["timezone"])
                ).day
                conn.execute(
                    "UPDATE reminders SET anchor_day=? WHERE id=?",
                    (local_day, legacy_row["id"]),
                )
            except Exception:
                # Xato vaqt mintaqasi bo'lsa, scheduler keyingi hisobda sananing joriy kunidan foydalanadi.
                continue

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, status, due_at)"
        )


def ensure_user(user_id: int, chat_id: int, username: str | None, first_name: str | None) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, chat_id, username, first_name, timezone, joined_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                chat_id=excluded.chat_id,
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (user_id, chat_id, username, first_name, config.DEFAULT_TIMEZONE, utc_now_iso()),
        )


def get_user(user_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def set_timezone(user_id: int, timezone_name: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE users SET timezone=? WHERE user_id=?", (timezone_name, user_id))
        conn.execute(
            "UPDATE reminders SET timezone=?, updated_at=? WHERE user_id=? AND status='active'",
            (timezone_name, utc_now_iso(), user_id),
        )


def add_reminder(
    user_id: int,
    title: str,
    due_at: datetime,
    timezone_name: str,
    repeat_rule: str,
    category: str,
    priority: str,
) -> int:
    now = utc_now_iso()
    anchor_day = due_at.astimezone(ZoneInfo(timezone_name)).day
    with connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reminders(
                user_id, title, due_at, timezone, repeat_rule, category,
                priority, anchor_day, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                user_id,
                title,
                due_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                timezone_name,
                repeat_rule,
                category,
                priority,
                anchor_day,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def count_active_for_user(user_id: int) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM reminders WHERE user_id=? AND status='active'",
            (user_id,),
        ).fetchone()
        return int(row["total"])


def get_reminder(reminder_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()


def list_reminders(user_id: int, limit: int = 10, offset: int = 0) -> list[sqlite3.Row]:
    with connection() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM reminders
                WHERE user_id=? AND status='active'
                ORDER BY COALESCE(snooze_until, due_at) ASC, id ASC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
        )


def search_reminders(user_id: int, query: str, limit: int = 10) -> list[sqlite3.Row]:
    with connection() as conn:
        return list(
            conn.execute(
                """
                SELECT * FROM reminders
                WHERE user_id=? AND status='active' AND title LIKE ?
                ORDER BY due_at ASC LIMIT ?
                """,
                (user_id, f"%{query}%", limit),
            ).fetchall()
        )


def complete_reminder(reminder_id: int, user_id: int) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders SET status='done', snooze_until=NULL, updated_at=?
            WHERE id=? AND user_id=? AND status='active' AND repeat_rule='none'
            """,
            (utc_now_iso(), reminder_id, user_id),
        )
        return cursor.rowcount > 0


def stop_reminder(reminder_id: int, user_id: int) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders SET status='done', snooze_until=NULL, updated_at=?
            WHERE id=? AND user_id=? AND status='active'
            """,
            (utc_now_iso(), reminder_id, user_id),
        )
        return cursor.rowcount > 0


def cancel_reminder(reminder_id: int, user_id: int) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders SET status='cancelled', snooze_until=NULL, updated_at=?
            WHERE id=? AND user_id=? AND status='active'
            """,
            (utc_now_iso(), reminder_id, user_id),
        )
        return cursor.rowcount > 0


def snooze_reminder(reminder_id: int, user_id: int, until: datetime) -> bool:
    """Bildirishnomadan turib vaqtincha kechiktirish; keyingi takroriy sana saqlanadi."""
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders SET snooze_until=?, updated_at=?
            WHERE id=? AND user_id=? AND status='active'
            """,
            (
                until.astimezone(timezone.utc).isoformat(timespec="seconds"),
                utc_now_iso(),
                reminder_id,
                user_id,
            ),
        )
        return cursor.rowcount > 0


def shift_reminder(reminder_id: int, user_id: int, new_due_at: datetime) -> bool:
    """Ro'yxatdan eslatma vaqtini ko'chirish; keyingi takrorlanish shu sanaga bog'lanadi."""
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders
            SET due_at=?, snooze_until=NULL, notified_at=NULL, updated_at=?
            WHERE id=? AND user_id=? AND status='active'
            """,
            (
                new_due_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                utc_now_iso(),
                reminder_id,
                user_id,
            ),
        )
        return cursor.rowcount > 0


def edit_reminder(
    reminder_id: int,
    user_id: int,
    title: str,
    due_at: datetime,
) -> bool:
    existing = get_reminder(reminder_id)
    if existing is None or int(existing["user_id"]) != user_id:
        return False
    anchor_day = due_at.astimezone(ZoneInfo(existing["timezone"])).day
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE reminders
            SET title=?, due_at=?, anchor_day=?, notified_at=NULL, snooze_until=NULL, updated_at=?
            WHERE id=? AND user_id=? AND status='active'
            """,
            (
                title,
                due_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                anchor_day,
                utc_now_iso(),
                reminder_id,
                user_id,
            ),
        )
        return cursor.rowcount > 0


def fetch_due_reminders(now: datetime) -> list[sqlite3.Row]:
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    with connection() as conn:
        return list(
            conn.execute(
                """
                SELECT r.*, u.chat_id
                FROM reminders r
                JOIN users u ON u.user_id=r.user_id
                WHERE r.status='active' AND (
                    (r.snooze_until IS NOT NULL AND r.snooze_until<=?) OR
                    (r.snooze_until IS NULL AND r.due_at<=? AND
                        (r.repeat_rule!='none' OR r.notified_at IS NULL))
                )
                ORDER BY COALESCE(r.snooze_until, r.due_at) ASC
                LIMIT 100
                """,
                (now_iso, now_iso),
            ).fetchall()
        )


def record_delivery(
    reminder_id: int,
    repeat_rule: str,
    next_due_at: datetime | None,
    was_snoozed: bool,
    now: datetime,
) -> None:
    """Xabar yuborishdan oldin holatni yangilab, takroriy yuborilishni cheklaydi."""
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    with connection() as conn:
        if repeat_rule == "none":
            conn.execute(
                """
                UPDATE reminders SET notified_at=?, snooze_until=NULL, updated_at=?
                WHERE id=? AND status='active'
                """,
                (now_iso, now_iso, reminder_id),
            )
        elif was_snoozed:
            # Takroriy eslatma allaqachon keyingi rejalashtirilgan sanaga ko'chirilgan.
            conn.execute(
                "UPDATE reminders SET notified_at=?, snooze_until=NULL, updated_at=? WHERE id=? AND status='active'",
                (now_iso, now_iso, reminder_id),
            )
        elif next_due_at is not None:
            conn.execute(
                """
                UPDATE reminders SET due_at=?, notified_at=?, snooze_until=NULL, updated_at=?
                WHERE id=? AND status='active'
                """,
                (
                    next_due_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    now_iso,
                    now_iso,
                    reminder_id,
                ),
            )


def admin_counts() -> dict[str, int]:
    with connection() as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active = conn.execute("SELECT COUNT(*) AS n FROM reminders WHERE status='active'").fetchone()["n"]
        all_reminders = conn.execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"]
        return {"users": int(users), "active": int(active), "all": int(all_reminders)}


def export_rows() -> list[sqlite3.Row]:
    with connection() as conn:
        return list(
            conn.execute(
                """
                SELECT r.id, r.user_id, u.username, r.title, r.due_at, r.timezone,
                       r.repeat_rule, r.category, r.priority, r.status, r.created_at
                FROM reminders r LEFT JOIN users u ON u.user_id=r.user_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        )
