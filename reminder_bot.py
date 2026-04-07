import logging
import re
import json
import os
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "8784078941:AAF_MA_s_YQIIYg9gVr7v_x_5o5NlulWT6E"
TIMEZONE = "Asia/Tashkent"
BOT_NAME = "Азим 2.0"
TASKS_FILE = "tasks.json"

logging.basicConfig(level=logging.INFO)

# Постоянная клавиатура внизу экрана
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Список"), KeyboardButton("✅ Выполнено")]],
    resize_keyboard=True
)

# ─── Хранилище задач ────────────────────────────────────────────────────────

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for chat_id, tasks in raw.items():
            seen = set()
            result[int(chat_id)] = []
            for t in tasks:
                if t["task"] in seen:
                    continue
                seen.add(t["task"])
                if t["remind_at"]:
                    tz = pytz.timezone(TIMEZONE)
                    t["remind_at"] = datetime.fromisoformat(t["remind_at"]).astimezone(tz)
                result[int(chat_id)].append(t)
        # Сохраняем сразу без дублей
        save_raw(result)
        return result
    return {}


def save_raw(tasks):
    serializable = {}
    for chat_id, task_list in tasks.items():
        serializable[str(chat_id)] = []
        for t in task_list:
            serializable[str(chat_id)].append({
                "task": t["task"],
                "remind_at": t["remind_at"].isoformat() if t["remind_at"] else None,
                "periodic": t["periodic"]
            })
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def save_tasks(tasks):
    serializable = {}
    for chat_id, task_list in tasks.items():
        serializable[str(chat_id)] = []
        for t in task_list:
            serializable[str(chat_id)].append({
                "task": t["task"],
                "remind_at": t["remind_at"].isoformat() if t["remind_at"] else None,
                "periodic": t["periodic"]
            })
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


TASKS = load_tasks()


def get_tasks(chat_id):
    return TASKS.get(chat_id, [])


def add_task(chat_id, task, remind_at, periodic=False):
    if chat_id not in TASKS:
        TASKS[chat_id] = []
    existing = [t["task"] for t in TASKS[chat_id]]
    if task not in existing:
        TASKS[chat_id].append({"task": task, "remind_at": remind_at, "periodic": periodic})
        save_tasks(TASKS)


def remove_task(chat_id, task):
    if chat_id in TASKS:
        TASKS[chat_id] = [t for t in TASKS[chat_id] if t["task"] != task]
        save_tasks(TASKS)


# ─── Парсинг времени ────────────────────────────────────────────────────────

def parse_reminder(text: str):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    remind_at = None
    task = text

    m = re.search(r'через (\d+)\s*(мин|минут|час|часов|ч\b)', text, re.IGNORECASE)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith('ч') else timedelta(minutes=amount)
        remind_at = now + delta
        task = (text[:m.start()] + text[m.end():]).strip()

    if not remind_at:
        m = re.search(r'завтра\s+в\s+(\d{1,2})[:\s](\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            remind_at = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
            task = (text[:m.start()] + text[m.end():]).strip()

    if not remind_at:
        m = re.search(r'(?:сегодня\s+)?в\s+(\d{1,2})[:\s](\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            remind_at = candidate
            task = (text[:m.start()] + text[m.end():]).strip()

    if not remind_at:
        m = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}[:\s]\d{2})', text)
        if m:
            date_str = m.group(1)
            time_str = m.group(2).replace(' ', ':')
            fmt = "%Y-%m-%d" if "-" in date_str else "%d.%m.%Y"
            dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M")
            remind_at = pytz.timezone(TIMEZONE).localize(dt)
            task = (text[:m.start()] + text[m.end():]).strip()

    if not remind_at:
        m = re.search(r'\b(\d{1,2})[:\s](\d{2})\b', text)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mi <= 59:
                candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if candidate <= now:
                    candidate += timedelta(days=1)
                remind_at = candidate
                task = (text[:m.start()] + text[m.end():]).strip()

    task = task.strip(" ,.-в")
    if not task:
        task = "Напоминание"

    return task, remind_at


def _next_periodic_time(now):
    candidate = now + timedelta(minutes=1)
    if candidate.hour < 10:
        candidate = candidate.replace(hour=10, minute=0, second=0, microsecond=0)
    elif candidate.hour >= 23:
        candidate = (candidate + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return (candidate - now).total_seconds()


# ─── Восстановление джобов ──────────────────────────────────────────────────

async def restore_jobs(app):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    restored = 0

    for chat_id, tasks in list(TASKS.items()):
        for t in list(tasks):
            task = t["task"]
            remind_at = t["remind_at"]
            periodic = t["periodic"]

            if periodic:
                app.job_queue.run_repeating(
                    send_periodic_reminder,
                    interval=timedelta(hours=4),
                    first=_next_periodic_time(now),
                    data={"chat_id": chat_id, "task": task},
                    name=f"periodic_{chat_id}_{task}"
                )
                restored += 1
            elif remind_at:
                offsets = [
                    (timedelta(hours=3), "⏰ До события *3 часа*"),
                    (timedelta(minutes=30), "⚡️ До события *30 минут*"),
                    (timedelta(minutes=5), "🔥 До события *5 минут*"),
                    (timedelta(0), "🔔 *Время пришло!*"),
                ]
                any_scheduled = False
                for delta, prefix in offsets:
                    fire_at = remind_at - delta
                    delay = (fire_at - now).total_seconds()
                    if delay > 0:
                        app.job_queue.run_once(
                            send_reminder,
                            when=delay,
                            data={"chat_id": chat_id, "task": task, "prefix": prefix},
                            name=f"{chat_id}_{task}_{delta.total_seconds()}"
                        )
                        any_scheduled = True
                        restored += 1
                if not any_scheduled:
                    remove_task(chat_id, task)

    logging.info(f"Восстановлено {restored} джобов")


# ─── Хэндлеры ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет! Я *{BOT_NAME}* — твой личный помощник.\n\n"
        "Напиши задачу со временем — напомню за *3 часа*, *30 минут* и *5 минут*:\n"
        "• позвонить Ване завтра в 15:00\n"
        "• сдать отчёт в 18 30\n\n"
        "Без времени — напоминаю каждые *4 часа* с 10:00 до 23:00:\n"
        "• выпить воду\n\n"
        "Используй кнопки внизу 👇",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Обработка кнопок клавиатуры
    if text == "📋 Список":
        await show_list(update, context)
        return
    if text == "✅ Выполнено":
        await show_done(update, context)
        return

    task, remind_at = parse_reminder(text)
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    chat_id = update.message.chat_id

    if remind_at:
        offsets = [
            (timedelta(hours=3), "⏰ До события *3 часа*"),
            (timedelta(minutes=30), "⚡️ До события *30 минут*"),
            (timedelta(minutes=5), "🔥 До события *5 минут*"),
            (timedelta(0), "🔔 *Время пришло!*"),
        ]

        scheduled = []
        for delta, prefix in offsets:
            fire_at = remind_at - delta
            delay = (fire_at - now).total_seconds()
            if delay > 0:
                context.job_queue.run_once(
                    send_reminder,
                    when=delay,
                    data={"chat_id": chat_id, "task": task, "prefix": prefix},
                    name=f"{chat_id}_{task}_{delta.total_seconds()}"
                )
                scheduled.append(prefix)

        add_task(chat_id, task, remind_at, periodic=False)
        time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
        reminders_text = "\n".join(f"  {s}" for s in scheduled)
        await update.message.reply_text(
            f"✅ Запомнил!\n\n📌 *{task}*\n📅 {time_str}\n\nНапомню:\n{reminders_text}",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )

    else:
        context.job_queue.run_repeating(
            send_periodic_reminder,
            interval=timedelta(hours=4),
            first=_next_periodic_time(now),
            data={"chat_id": chat_id, "task": task},
            name=f"periodic_{chat_id}_{task}"
        )
        add_task(chat_id, task, None, periodic=True)
        await update.message.reply_text(
            f"🔁 Запомнил как *бессрочную задачу*!\n\n📌 *{task}*\n\n"
            f"Буду напоминать каждые *4 часа* с 10:00 до 23:00.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    tasks = get_tasks(chat_id)

    if not tasks:
        await update.message.reply_text("📭 Активных напоминаний нет.", reply_markup=MAIN_KEYBOARD)
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    timed = sorted([t for t in tasks if t["remind_at"]], key=lambda x: x["remind_at"])
    periodic = [t for t in tasks if not t["remind_at"]]
    all_tasks = timed + periodic

    lines = [f"📋 *Все напоминания ({BOT_NAME}):*\n"]

    if timed:
        lines.append("*⏰ По времени:*")
        for i, t in enumerate(timed, 1):
            time_str = t["remind_at"].strftime("%d.%m в %H:%M")
            diff = t["remind_at"] - now
            total_min = int(diff.total_seconds() / 60)
            if total_min <= 0:
                left = "уже прошло"
            elif total_min < 60:
                left = f"через {total_min} мин"
            elif total_min < 1440:
                left = f"через {total_min // 60} ч {total_min % 60} мин"
            else:
                left = f"через {total_min // 1440} д"
            lines.append(f"{i}. 📌 *{t['task']}*\n    📅 {time_str} ({left})")

    if periodic:
        if timed:
            lines.append("")
        lines.append("*🔁 Бессрочные:*")
        for i, t in enumerate(periodic, 1):
            lines.append(f"{i}. 🔁 *{t['task']}*")

    # Inline кнопки удаления под списком
    keyboard = []
    for t in all_tasks:
        # Обрезаем название до 30 символов для кнопки
        short = t["task"][:30]
        keyboard.append([InlineKeyboardButton(f"✅ {short}", callback_data=f"d_{t['task'][:50]}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def show_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    tasks = get_tasks(chat_id)

    if not tasks:
        await update.message.reply_text("📭 Нет активных задач.", reply_markup=MAIN_KEYBOARD)
        return

    tz = pytz.timezone(TIMEZONE)
    timed = sorted([t for t in tasks if t["remind_at"]], key=lambda x: x["remind_at"])
    periodic = [t for t in tasks if not t["remind_at"]]
    all_tasks = timed + periodic

    keyboard = []
    for t in all_tasks:
        if t["remind_at"]:
            label = f"📌 {t['task'][:25]} ({t['remind_at'].strftime('%d.%m %H:%M')})"
        else:
            label = f"🔁 {t['task'][:30]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"d_{t['task'][:50]}")])

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="d_cancel")])

    await update.message.reply_text(
        "✅ Какую задачу отметить как выполненную?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "d_cancel":
        await query.edit_message_text("Отменено.")
        return

    task_name = query.data[2:]  # убираем префикс "d_"

    # Ищем полное название задачи (callback обрезан до 50 символов)
    tasks = get_tasks(chat_id)
    full_task = None
    for t in tasks:
        if t["task"].startswith(task_name) or t["task"][:50] == task_name:
            full_task = t["task"]
            break

    if not full_task:
        await query.edit_message_text("⚠️ Задача не найдена.")
        return

    # Останавливаем все джобы задачи
    jobs = context.job_queue.jobs()
    for job in jobs:
        if job.data.get("task") == full_task and job.data.get("chat_id") == chat_id:
            job.schedule_removal()

    remove_task(chat_id, full_task)

    compliments = [
        "🔥 Огонь, так держать!",
        "💪 Молодец, не останавливайся!",
        "⚡️ Красавчик, задача закрыта!",
        "🏆 Победа! Ты справился!",
        "✨ Отлично сделано!"
    ]
    import random
    compliment = random.choice(compliments)

    await query.edit_message_text(
        f"{compliment}\n\n✅ *{full_task}* — выполнено!",
        parse_mode="Markdown"
    )


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    task = job.data["task"]
    prefix = job.data["prefix"]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{prefix}\n\n📌 *{task}*",
        parse_mode="Markdown"
    )


async def send_periodic_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    task = job.data["task"]
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    if 10 <= now.hour < 23:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 *Напоминание!*\n\n📌 *{task}*",
            parse_mode="Markdown"
        )


async def stop_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    jobs = context.job_queue.jobs()
    for job in jobs:
        if job.data.get("chat_id") == chat_id:
            job.schedule_removal()
    TASKS.pop(chat_id, None)
    save_tasks(TASKS)
    await update.message.reply_text("🛑 Все напоминания остановлены.", reply_markup=MAIN_KEYBOARD)


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(restore_jobs).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", show_list))
    app.add_handler(CommandHandler("done", show_done))
    app.add_handler(CommandHandler("stop", stop_all))
    app.add_handler(CallbackQueryHandler(done_callback, pattern=r"^d_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"{BOT_NAME} запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
