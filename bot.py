"""Remind Me — Telegram eslatma boti.

Ishga tushirish: python bot.py (Render Background Worker).
Barcha eslatmalar lokal SQLite bazasida saqlanadi.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database
from scheduler import DateInputError, format_datetime, get_zone, next_occurrence, parse_due_text

# ConversationHandler bosqichlari
NEW_TITLE, NEW_WHEN, NEW_REPEAT, NEW_CATEGORY, NEW_PRIORITY = range(5)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).resolve().parent / "bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("remind_me")

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Yangi eslatma"), KeyboardButton("📋 Eslatmalarim")],
        [KeyboardButton("📅 Bugungi"), KeyboardButton("🔎 Qidirish")],
        [KeyboardButton("⚙️ Sozlamalar"), KeyboardButton("ℹ️ Yordam")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

CATEGORY_LABELS = {
    "personal": "👤 Shaxsiy",
    "work": "💼 Ish",
    "study": "📚 O'qish",
    "health": "❤️ Sog'liq",
    "other": "📌 Boshqa",
}
CATEGORY_KEYS = {value: key for key, value in CATEGORY_LABELS.items()}
PRIORITY_LABELS = {"low": "🟢 Past", "normal": "🟡 O'rtacha", "high": "🔴 Muhim"}
REPEAT_LABELS = {
    "none": "Bir marta",
    "daily": "Har kuni",
    "weekdays": "Har ish kuni (Du–Ju)",
    "weekly": "Har hafta",
    "monthly": "Har oy",
}


def _user_info(update: Update) -> tuple[int, int, str | None, str | None]:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        raise ValueError("Foydalanuvchi yoki chat aniqlanmadi")
    return user.id, chat.id, user.username, user.first_name


def register_user(update: Update) -> int:
    user_id, chat_id, username, first_name = _user_info(update)
    database.ensure_user(user_id, chat_id, username, first_name)
    return user_id


def user_timezone(user_id: int) -> str:
    user = database.get_user(user_id)
    return user["timezone"] if user else config.DEFAULT_TIMEZONE


def reminder_text(row, timezone_name: str | None = None) -> str:
    tz_name = timezone_name or row["timezone"]
    when_value = row["snooze_until"] or row["due_at"]
    when_text = format_datetime(when_value, tz_name)
    repeat = REPEAT_LABELS.get(row["repeat_rule"], row["repeat_rule"])
    category = CATEGORY_LABELS.get(row["category"], CATEGORY_LABELS["other"])
    priority = PRIORITY_LABELS.get(row["priority"], PRIORITY_LABELS["normal"])
    return (
        f"<b>#{row['id']} — {escape_html(row['title'])}</b>\n"
        f"🕒 {when_text}\n"
        f"🔁 {repeat}  |  {category}\n"
        f"⚑ {priority}"
    )


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def reminder_keyboard(row) -> InlineKeyboardMarkup:
    rid = int(row["id"])
    buttons = [
        [
            InlineKeyboardButton("✅ Bajarildi", callback_data=f"r:done:{rid}"),
            InlineKeyboardButton("⏰ 5 daq", callback_data=f"r:snooze:{rid}:5"),
            InlineKeyboardButton("⏰ 10 daq", callback_data=f"r:snooze:{rid}:10"),
        ],
        [
            InlineKeyboardButton("⏰ 30 daq", callback_data=f"r:snooze:{rid}:30"),
            InlineKeyboardButton("⏰ 1 soat", callback_data=f"r:snooze:{rid}:60"),
        ],
        [
            InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"r:edit:{rid}"),
            InlineKeyboardButton("🗑 O'chirish", callback_data=f"r:delete:{rid}"),
        ],
    ]
    if row["repeat_rule"] != "none":
        buttons.append([InlineKeyboardButton("🛑 Takrorlashni to'xtatish", callback_data=f"r:stop:{rid}")])
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    tz_name = user_timezone(user_id)
    await update.message.reply_text(
        "<b>Remind Me</b> botiga xush kelibsiz! 🔔\n\n"
        "Muhim ishlaringizni yozing — belgilangan vaqtda Telegram orqali eslataman.\n\n"
        "➕ Yangi eslatma yaratish uchun tugmani bosing yoki /new yozing.\n"
        f"🕒 Vaqt mintaqasi: <code>{escape_html(tz_name)}</code>\n\n"
        "Eslatmalaringiz kompyuteringizdagi lokal bazada saqlanadi.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update)
    text = (
        "<b>Remind Me — buyruqlar</b>\n\n"
        "/new — yangi eslatma\n"
        "/list — eslatmalar ro'yxati\n"
        "/today — bugungi eslatmalar\n"
        "/search so'z — eslatma qidirish\n"
        "/stats — shaxsiy statistika\n"
        "/timezone Asia/Tashkent — vaqt mintaqasini o'zgartirish\n"
        "/cancel — yangi eslatma yaratishni bekor qilish\n\n"
        "<b>Vaqt kiritish namunalari</b>\n"
        "• <code>20.11.2026 18:30</code>\n"
        "• <code>2026-11-20 18:30</code>\n"
        "• <code>18:30</code> (vaqt o'tgan bo'lsa, ertaga)\n"
        "• <code>ertaga 09:00</code>\n"
        "• <code>30m</code>, <code>2h</code>, <code>1d</code>\n\n"
        "Takrorlash: bir marta, har kuni, ish kunlari, haftalik yoki oylik."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_MENU)


async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = register_user(update)
    if database.count_active_for_user(user_id) >= config.MAX_REMINDERS_PER_USER:
        await update.effective_message.reply_text(
            f"Sizda faol eslatmalar soni {config.MAX_REMINDERS_PER_USER} taga yetdi. Avval eskilarini o'chiring.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    context.user_data["draft"] = {}
    await update.effective_message.reply_text(
        "📝 <b>Eslatma matnini kiriting.</b>\nMasalan: Ertaga loyiha topshirish yoki Onamga qo'ng'iroq qilish.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NEW_TITLE


async def new_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = (update.message.text or "").strip()
    if not title or len(title) > 200:
        await update.message.reply_text("Eslatma matni 1–200 belgi oralig'ida bo'lishi kerak. Qayta kiriting:")
        return NEW_TITLE
    context.user_data.setdefault("draft", {})["title"] = title
    user_id = register_user(update)
    tz_name = user_timezone(user_id)
    await update.message.reply_text(
        "🕒 <b>Qachon eslatay?</b>\n\n"
        "Sana-vaqtni kiriting: <code>20.11.2026 18:30</code>\n"
        "Yoki: <code>18:30</code>, <code>ertaga 09:00</code>, <code>30m</code>, <code>2h</code>.\n\n"
        f"Vaqt mintaqasi: <code>{escape_html(tz_name)}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("10 daqiqadan keyin", callback_data="quick:10m"),
                    InlineKeyboardButton("30 daqiqadan keyin", callback_data="quick:30m"),
                ],
                [
                    InlineKeyboardButton("1 soatdan keyin", callback_data="quick:1h"),
                    InlineKeyboardButton("Ertaga 09:00", callback_data="quick:tomorrow"),
                ],
            ]
        ),
    )
    return NEW_WHEN


async def _after_when(update: Update, context: ContextTypes.DEFAULT_TYPE, due_at: datetime) -> int:
    context.user_data["draft"]["due_at"] = due_at
    await update.effective_message.reply_text(
        "🔁 <b>Takrorlanish turini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Bir marta", callback_data="repeat:none")],
                [
                    InlineKeyboardButton("Har kuni", callback_data="repeat:daily"),
                    InlineKeyboardButton("Ish kunlari", callback_data="repeat:weekdays"),
                ],
                [
                    InlineKeyboardButton("Har hafta", callback_data="repeat:weekly"),
                    InlineKeyboardButton("Har oy", callback_data="repeat:monthly"),
                ],
            ]
        ),
    )
    return NEW_REPEAT


async def new_when_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = register_user(update)
    try:
        due_at = parse_due_text(update.message.text or "", user_timezone(user_id))
    except DateInputError as exc:
        await update.message.reply_text(f"⚠️ {exc}\nYana bir marta kiriting:")
        return NEW_WHEN
    return await _after_when(update, context, due_at)


async def quick_when_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = register_user(update)
    value = query.data.split(":", 1)[1]
    if value == "tomorrow":
        text = "ertaga 09:00"
    else:
        text = value
    try:
        due_at = parse_due_text(text, user_timezone(user_id))
    except DateInputError as exc:
        await query.message.reply_text(f"⚠️ {exc}")
        return NEW_WHEN
    await query.message.reply_text(f"🕒 Vaqt tanlandi: {format_datetime(due_at, user_timezone(user_id))}")
    return await _after_when(update, context, due_at)


async def repeat_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    rule = query.data.split(":", 1)[1]
    if rule not in REPEAT_LABELS:
        await query.message.reply_text("Noto'g'ri takrorlanish turi. /new bilan qaytadan boshlang.")
        return ConversationHandler.END
    context.user_data["draft"]["repeat_rule"] = rule
    await query.message.reply_text(
        "📂 <b>Kategoriyani tanlang:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("👤 Shaxsiy", callback_data="category:personal"),
                    InlineKeyboardButton("💼 Ish", callback_data="category:work"),
                ],
                [
                    InlineKeyboardButton("📚 O'qish", callback_data="category:study"),
                    InlineKeyboardButton("❤️ Sog'liq", callback_data="category:health"),
                ],
                [InlineKeyboardButton("📌 Boshqa", callback_data="category:other")],
            ]
        ),
    )
    return NEW_CATEGORY


async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    if category not in CATEGORY_LABELS:
        return NEW_CATEGORY
    context.user_data["draft"]["category"] = category
    await query.message.reply_text(
        "⚑ <b>Muhimlik darajasini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🟢 Past", callback_data="priority:low"),
                    InlineKeyboardButton("🟡 O'rtacha", callback_data="priority:normal"),
                    InlineKeyboardButton("🔴 Muhim", callback_data="priority:high"),
                ]
            ]
        ),
    )
    return NEW_PRIORITY


async def priority_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    priority = query.data.split(":", 1)[1]
    if priority not in PRIORITY_LABELS:
        return NEW_PRIORITY

    user_id = register_user(update)
    draft = context.user_data.get("draft", {})
    if not all(key in draft for key in ("title", "due_at", "repeat_rule", "category")):
        await query.message.reply_text("Ma'lumotlar to'liq emas. /new buyrug'i bilan qaytadan boshlang.", reply_markup=MAIN_MENU)
        context.user_data.pop("draft", None)
        return ConversationHandler.END

    draft["priority"] = priority
    tz_name = user_timezone(user_id)
    reminder_id = database.add_reminder(
        user_id=user_id,
        title=draft["title"],
        due_at=draft["due_at"],
        timezone_name=tz_name,
        repeat_rule=draft["repeat_rule"],
        category=draft["category"],
        priority=priority,
    )
    due_text = format_datetime(draft["due_at"], tz_name)
    repeat_text = REPEAT_LABELS[draft["repeat_rule"]]
    await query.message.reply_text(
        f"✅ <b>Eslatma saqlandi!</b>\n\n"
        f"🆔 ID: <code>{reminder_id}</code>\n"
        f"📝 {escape_html(draft['title'])}\n"
        f"🕒 {due_text}\n"
        f"🔁 {repeat_text}\n\n"
        "Belgilangan vaqtda xabar yuboraman.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )
    context.user_data.pop("draft", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("draft", None)
    await update.effective_message.reply_text(
        "Eslatma yaratish bekor qilindi.", reply_markup=MAIN_MENU
    )
    return ConversationHandler.END


def _list_markup(rows) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        rid = int(row["id"])
        done_label = f"✅ #{rid}" if row["repeat_rule"] == "none" else f"✅ Bugun #{rid}"
        keyboard.append(
            [
                InlineKeyboardButton(done_label, callback_data=f"r:done:{rid}"),
                InlineKeyboardButton("⏰ +10", callback_data=f"r:shift:{rid}:10"),
                InlineKeyboardButton("✏️", callback_data=f"r:edit:{rid}"),
                InlineKeyboardButton("🗑", callback_data=f"r:delete:{rid}"),
            ]
        )
    return InlineKeyboardMarkup(keyboard)


async def send_reminder_list(message, user_id: int, offset: int = 0, title: str = "📋 <b>Eslatmalaringiz</b>") -> None:
    rows = database.list_reminders(user_id, limit=8, offset=offset)
    if not rows:
        await message.reply_text(
            "Hozircha faol eslatma yo'q. ➕ Yangi eslatma tugmasini bosing.",
            reply_markup=MAIN_MENU,
        )
        return
    blocks = [reminder_text(row) for row in rows]
    text = title + "\n\n" + "\n\n──────────────\n\n".join(blocks)
    total_active = database.count_active_for_user(user_id)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page:{max(0, offset - 8)}"))
    if offset + len(rows) < total_active:
        nav.append(InlineKeyboardButton("Keyingisi ➡️", callback_data=f"page:{offset + 8}"))
    keyboard = [list(row) for row in _list_markup(rows).inline_keyboard]
    if nav:
        keyboard.append(nav)
    await message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    offset = 0
    if context.args:
        try:
            offset = max(0, (int(context.args[0]) - 1) * 8)
        except ValueError:
            offset = 0
    await send_reminder_list(update.effective_message, user_id, offset)


async def list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    await send_reminder_list(update.message, user_id)


async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = register_user(update)
    try:
        offset = max(0, int(query.data.split(":", 1)[1]))
    except ValueError:
        offset = 0
    rows = database.list_reminders(user_id, limit=8, offset=offset)
    if not rows:
        await query.edit_message_text("Bu sahifada eslatma topilmadi.")
        return
    text = "📋 <b>Eslatmalaringiz</b>\n\n" + "\n\n──────────────\n\n".join(reminder_text(row) for row in rows)
    total = database.count_active_for_user(user_id)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page:{max(0, offset - 8)}"))
    if offset + len(rows) < total:
        nav.append(InlineKeyboardButton("Keyingisi ➡️", callback_data=f"page:{offset + 8}"))
    keyboard = [list(row) for row in _list_markup(rows).inline_keyboard]
    if nav:
        keyboard.append(nav)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    tz_name = user_timezone(user_id)
    zone = get_zone(tz_name)
    today = datetime.now(zone).date()
    rows = database.list_reminders(user_id, limit=500, offset=0)
    today_rows = []
    for row in rows:
        effective = row["snooze_until"] or row["due_at"]
        due = datetime.fromisoformat(effective).astimezone(zone)
        if due.date() == today:
            today_rows.append(row)
    if not today_rows:
        await update.effective_message.reply_text("📅 Bugun uchun faol eslatma yo'q.", reply_markup=MAIN_MENU)
        return
    text = "📅 <b>Bugungi eslatmalar</b>\n\n" + "\n\n──────────────\n\n".join(
        reminder_text(row, tz_name) for row in today_rows[:8]
    )
    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_list_markup(today_rows[:8]),
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    query_text = " ".join(context.args).strip()
    if not query_text:
        context.user_data["search_pending"] = True
        await update.effective_message.reply_text("🔎 Qidiriladigan so'zni yuboring:")
        return
    await show_search_results(update.effective_message, user_id, query_text)


async def show_search_results(message, user_id: int, query_text: str) -> None:
    rows = database.search_reminders(user_id, query_text, limit=8)
    if not rows:
        await message.reply_text(f"'{escape_html(query_text)}' bo'yicha eslatma topilmadi.", parse_mode="HTML")
        return
    text = f"🔎 <b>Qidiruv: {escape_html(query_text)}</b>\n\n" + "\n\n──────────────\n\n".join(
        reminder_text(row) for row in rows
    )
    await message.reply_text(text, parse_mode="HTML", reply_markup=_list_markup(rows))


async def search_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update)
    context.user_data["search_pending"] = True
    await update.message.reply_text("🔎 Qidiriladigan so'zni yuboring:")


async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tahrirlash va menyudagi qidiruv uchun erkin matn kiritish."""
    user_id = register_user(update)
    text = (update.message.text or "").strip()

    if context.user_data.get("search_pending"):
        context.user_data.pop("search_pending", None)
        await show_search_results(update.message, user_id, text)
        return

    edit_id = context.user_data.get("edit_id")
    if edit_id:
        if "|" not in text:
            await update.message.reply_text(
                "Format: <code>Yangi eslatma nomi | 20.11.2026 18:30</code>\n"
                "Vaqt o'rniga <code>ertaga 09:00</code> yoki <code>30m</code> ham yozish mumkin.",
                parse_mode="HTML",
            )
            return
        new_title, new_time = [part.strip() for part in text.split("|", 1)]
        if not new_title or len(new_title) > 200:
            await update.message.reply_text("Yangi nom 1–200 belgi bo'lishi kerak. Qayta yuboring:")
            return
        try:
            due_at = parse_due_text(new_time, user_timezone(user_id))
        except DateInputError as exc:
            await update.message.reply_text(f"⚠️ {exc}\nQayta yuboring:")
            return
        edited = database.edit_reminder(int(edit_id), user_id, new_title, due_at)
        context.user_data.pop("edit_id", None)
        if edited:
            await update.message.reply_text("✅ Eslatma yangilandi.", reply_markup=MAIN_MENU)
        else:
            await update.message.reply_text("Eslatma topilmadi yoki endi faol emas.", reply_markup=MAIN_MENU)
        return


async def reminder_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = register_user(update)
    parts = query.data.split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    try:
        reminder_id = int(parts[2])
    except ValueError:
        return
    row = database.get_reminder(reminder_id)
    if row is None or int(row["user_id"]) != user_id or row["status"] != "active":
        await query.message.reply_text("Eslatma topilmadi yoki u sizga tegishli emas.")
        return

    if action == "done":
        if row["repeat_rule"] == "none":
            database.complete_reminder(reminder_id, user_id)
            response = "✅ Eslatma bajarildi va faol ro'yxatdan chiqarildi."
        else:
            response = "✅ Bugungi xabar belgilandi. Takroriy eslatma jadval bo'yicha davom etadi."
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(response)
    elif action == "stop":
        database.stop_reminder(reminder_id, user_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("🛑 Takroriy eslatma to'xtatildi.")
    elif action == "shift" and len(parts) == 4:
        try:
            minutes = int(parts[3])
        except ValueError:
            return
        if minutes not in (5, 10, 15, 30, 60, 120):
            return
        new_due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        database.shift_reminder(reminder_id, user_id, new_due)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"⏰ Eslatma vaqti {minutes} daqiqaga ko'chirildi.")
    elif action == "snooze" and len(parts) == 4:
        try:
            minutes = int(parts[3])
        except ValueError:
            return
        if minutes not in (5, 10, 15, 30, 60, 120):
            return
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        database.snooze_reminder(reminder_id, user_id, until)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"⏰ Xo'p, {minutes} daqiqadan keyin yana eslataman.")
    elif action == "edit":
        context.user_data["edit_id"] = reminder_id
        await query.message.reply_text(
            "✏️ Yangi nom va vaqtni bitta qatorda yuboring:\n"
            "<code>Yangi nom | 20.11.2026 18:30</code>\n\n"
            "Masalan: <code>Hisobotni topshirish | ertaga 09:00</code>",
            parse_mode="HTML",
        )
    elif action == "delete":
        await query.message.reply_text(
            f"#{reminder_id} eslatmani o'chiraymi?",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("Ha, o'chir", callback_data=f"r:delok:{reminder_id}"),
                    InlineKeyboardButton("Yo'q", callback_data=f"r:keep:{reminder_id}"),
                ]]
            ),
        )
    elif action == "delok":
        database.cancel_reminder(reminder_id, user_id)
        await query.edit_message_text("🗑 Eslatma o'chirildi.")
    elif action == "keep":
        await query.edit_message_text("Eslatma saqlab qolindi.")


async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    if not context.args:
        await update.effective_message.reply_text(
            f"Hozirgi vaqt mintaqangiz: <code>{escape_html(user_timezone(user_id))}</code>\n"
            "O'zgartirish: <code>/timezone Asia/Tashkent</code>",
            parse_mode="HTML",
        )
        return
    timezone_name = context.args[0]
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        await update.effective_message.reply_text("Vaqt mintaqasi topilmadi. Masalan: Asia/Tashkent yoki Europe/Moscow")
        return
    database.set_timezone(user_id, timezone_name)
    await update.effective_message.reply_text(
        f"✅ Vaqt mintaqasi <code>{escape_html(timezone_name)}</code> ga o'zgartirildi.",
        parse_mode="HTML",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    rows = database.list_reminders(user_id, limit=500, offset=0)
    repeated = sum(1 for row in rows if row["repeat_rule"] != "none")
    await update.effective_message.reply_text(
        "📊 <b>Shaxsiy statistika</b>\n\n"
        f"Faol eslatmalar: <b>{len(rows)}</b>\n"
        f"Takroriy eslatmalar: <b>{repeated}</b>\n"
        f"Vaqt mintaqasi: <code>{escape_html(user_timezone(user_id))}</code>",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    await update.message.reply_text(
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"Vaqt mintaqasi: <code>{escape_html(user_timezone(user_id))}</code>\n\n"
        "O'zgartirish uchun, masalan:\n<code>/timezone Asia/Tashkent</code>\n"
        "Yordam: /help",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await update.effective_message.reply_text("Bu buyruq faqat bot administratoriga mo'ljallangan.")
        return
    counts = database.admin_counts()
    await update.effective_message.reply_text(
        "🛠 <b>Admin panel</b>\n\n"
        f"Foydalanuvchilar: <b>{counts['users']}</b>\n"
        f"Faol eslatmalar: <b>{counts['active']}</b>\n"
        f"Jami eslatmalar: <b>{counts['all']}</b>\n\n"
        "/export — bazadagi eslatmalarni CSV fayl qilib olish",
        parse_mode="HTML",
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = register_user(update)
    if not config.ADMIN_ID or user_id != config.ADMIN_ID:
        await update.effective_message.reply_text("Bu buyruq faqat bot administratoriga mo'ljallangan.")
        return
    rows = database.export_rows()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id", "user_id", "username", "title", "due_at_utc", "timezone", "repeat", "category", "priority", "status", "created_at"])
    for row in rows:
        writer.writerow([row[key] for key in row.keys()])
    buffer = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    buffer.name = "remind_me_export.csv"
    await update.effective_message.reply_document(
        document=buffer,
        filename="remind_me_export.csv",
        caption="Remind Me bazasidan CSV eksport.",
    )


async def reminder_worker(application: Application) -> None:
    """SQLite'dagi muddati kelgan eslatmalarni tekshirib, Telegram'ga yuboradi."""
    while True:
        now = datetime.now(timezone.utc)
        try:
            due_rows = database.fetch_due_reminders(now)
            for row in due_rows:
                reminder_id = int(row["id"])
                repeat_rule = row["repeat_rule"]
                was_snoozed = bool(row["snooze_until"])
                next_due = None
                if repeat_rule != "none" and not was_snoozed:
                    current_due = datetime.fromisoformat(row["due_at"])
                    try:
                        next_due = next_occurrence(current_due, repeat_rule, row["timezone"], now, row["anchor_day"])
                    except Exception:
                        logger.exception("Takroriy eslatma sanasini hisoblashda xato: id=%s", reminder_id)
                        continue

                # Avval bazani yangilash takroran yuborilish ehtimolini kamaytiradi.
                database.record_delivery(reminder_id, repeat_rule, next_due, was_snoozed, now)
                actual_time = row["snooze_until"] or row["due_at"]
                when_text = format_datetime(actual_time, row["timezone"])
                category = CATEGORY_LABELS.get(row["category"], CATEGORY_LABELS["other"])
                priority = PRIORITY_LABELS.get(row["priority"], PRIORITY_LABELS["normal"])
                next_text = ""
                if repeat_rule != "none" and next_due is not None:
                    next_text = f"\n\n🔁 Keyingi eslatma: {format_datetime(next_due, row['timezone'])}"
                message_text = (
                    "🔔 <b>REMIND ME — ESLATMA!</b>\n\n"
                    f"📝 <b>{escape_html(row['title'])}</b>\n"
                    f"🕒 {when_text}\n"
                    f"{category}  |  {priority}"
                    f"{next_text}\n\n"
                    "Kerakli amalni tanlang:"
                )
                buttons = [
                    [
                        InlineKeyboardButton("✅ Bajarildi", callback_data=f"r:done:{reminder_id}"),
                        InlineKeyboardButton("⏰ +5 daq", callback_data=f"r:snooze:{reminder_id}:5"),
                        InlineKeyboardButton("⏰ +10 daq", callback_data=f"r:snooze:{reminder_id}:10"),
                    ],
                    [
                        InlineKeyboardButton("⏰ +30 daq", callback_data=f"r:snooze:{reminder_id}:30"),
                        InlineKeyboardButton("⏰ +1 soat", callback_data=f"r:snooze:{reminder_id}:60"),
                    ],
                ]
                if repeat_rule != "none":
                    buttons.append([InlineKeyboardButton("🛑 Takrorlashni to'xtatish", callback_data=f"r:stop:{reminder_id}")])
                buttons.append([InlineKeyboardButton("🗑 O'chirish", callback_data=f"r:delete:{reminder_id}")])
                try:
                    await application.bot.send_message(
                        chat_id=int(row["chat_id"]),
                        text=message_text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
                except Exception:
                    logger.exception("Eslatma yuborilmadi: id=%s, user_id=%s", reminder_id, row["user_id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Eslatmalarni tekshirishda umumiy xato")
        await asyncio.sleep(max(2, int(config.POLL_INTERVAL_SECONDS)))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Telegram update ishlovida xato: %s", context.error, exc_info=context.error)


async def post_init(application: Application) -> None:
    database.init_db()
    commands = [
        BotCommand("start", "Botni boshlash"),
        BotCommand("new", "Yangi eslatma yaratish"),
        BotCommand("list", "Eslatmalarim"),
        BotCommand("today", "Bugungi eslatmalar"),
        BotCommand("search", "Eslatma qidirish"),
        BotCommand("stats", "Shaxsiy statistika"),
        BotCommand("timezone", "Vaqt mintaqasini sozlash"),
        BotCommand("help", "Yordam"),
        BotCommand("cancel", "Amalni bekor qilish"),
    ]
    await application.bot.set_my_commands(commands)
    application.create_task(reminder_worker(application), name="remind-me-worker")
    logger.info("Remind Me ishga tushdi. Vaqt mintaqasi: %s", config.DEFAULT_TIMEZONE)


def build_application() -> Application:
    token = config.BOT_TOKEN.strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Render Dashboard > Environment bo'limida "
            "BOT_TOKEN qiymatini kiriting."
        )

    application = Application.builder().token(token).post_init(post_init).build()

    # Oddiy buyruqlar va menyu tugmalari.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("timezone", timezone_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(MessageHandler(filters.Regex(r"^📋 Eslatmalarim$"), list_menu))
    application.add_handler(MessageHandler(filters.Regex(r"^📅 Bugungi$"), today_command))
    application.add_handler(MessageHandler(filters.Regex(r"^🔎 Qidirish$"), search_menu))
    application.add_handler(MessageHandler(filters.Regex(r"^⚙️ Sozlamalar$"), settings_menu))
    application.add_handler(MessageHandler(filters.Regex(r"^ℹ️ Yordam$"), help_command))

    # Eslatma yaratish jarayoni.
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("new", new_start),
            MessageHandler(filters.Regex(r"^➕ Yangi eslatma$"), new_start),
        ],
        states={
            NEW_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_title)],
            NEW_WHEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_when_text),
                CallbackQueryHandler(quick_when_callback, pattern=r"^quick:"),
            ],
            NEW_REPEAT: [CallbackQueryHandler(repeat_choice, pattern=r"^repeat:")],
            NEW_CATEGORY: [CallbackQueryHandler(category_choice, pattern=r"^category:")],
            NEW_PRIORITY: [CallbackQueryHandler(priority_choice, pattern=r"^priority:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
    )
    application.add_handler(CallbackQueryHandler(reminder_action_callback, pattern=r"^r:"))
    application.add_handler(CallbackQueryHandler(page_callback, pattern=r"^page:"))
    application.add_handler(conversation)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    try:
        database.init_db()
        application = build_application()
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    except KeyboardInterrupt:
        logger.info("Foydalanuvchi botni to'xtatdi.")
    except Exception as exc:
        logger.exception("Botni ishga tushirishda xato: %s", exc)
        raise


if __name__ == "__main__":
    main()
