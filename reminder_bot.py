import logging
import re
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

TOKEN = "8784078941:AAF_MA_s_YQIIYg9gVr7v_x_5o5NlulWT6E"
TIMEZONE = "Asia/Tashkent"
BOT_NAME = "Азим 2.0"

logging.basicConfig(level=logging.INFO)


def parse_reminder(text: str):
    """
    Парсит задачу и время из текста.
    Возвращает (task, remind_at или None)
    None = бессрочная задача (напоминать каждые 4 часа с 10:00 до 23:00)
    """
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    remind_at = None
    task = text

    # через N минут/часов
    m = re.search(r'через (\d+)\s*(мин|минут|час|часов|ч\b)', text, re.IGNORECASE)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith('ч') else timedelta(minutes=amount)
        remind_at = now + delta
        task = (text[:m.start()] + text[m.end():]).strip()

    # завтра в HH:MM или завтра в HH MM
    if not remind_at:
        m = re.search(r'завтра\s+в\s+(\d{1,2})[:\s](\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            remind_at = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
            task = (text[:m.start()] + text[m.end():]).strip()

    # сегодня в HH:MM или в HH MM или в HH:MM
    if not remind_at:
        m = re.search(r'(?:сегодня\s+)?в\s+(\d{1,2})[:\s](\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            remind_at = candidate
            task = (text[:m.start()] + text[m.end():]).strip()

    # YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM
    if not remind_at:
        m = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}[:\s]\d{2})', text)
        if m:
            date_str = m.group(1)
            time_str = m.group(2).replace(' ', ':')
            fmt = "%Y-%m-%d" if "-" in date_str else "%d.%m.%Y"
            dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M")
            remind_at = pytz.timezone(TIMEZONE).localize(dt)
            task = (text[:m.start()] + text[m.end():]).strip()

    # просто HH MM или HH:MM без слова "в"
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Привет! Я *{BOT_NAME}* — твой личный помощник-напоминалка.\n\n"
        "Напиши задачу со временем, и я напомню тебе за *3 часа*, *30 минут* и *5 минут* до события:\n"
        "• купить молоко через 30 минут\n"
        "• позвонить Ване завтра в 15:00\n"
        "• сдать отчёт в 18 30\n"
        "• митинг 2025-04-01 10:00\n\n"
        "Если напишешь задачу *без времени* — буду напоминать каждые 4 часа с 10:00 до 23:00:\n"
        "• выпить воду\n"
        "• проверить почту\n\n"
        "Команды:\n"
        "/list — активные напоминания\n"
        "/stop — остановить все напоминания",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    task, remind_at = parse_reminder(text)
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    chat_id = update.message.chat_id

    if remind_at:
        # Напоминания за 3 часа, 30 минут, 5 минут и в момент события
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
                job_name = f"{chat_id}_{task}_{delta.total_seconds()}"
                context.job_queue.run_once(
                    send_reminder,
                    when=delay,
                    data={"chat_id": chat_id, "task": task, "prefix": prefix},
                    name=job_name
                )
                scheduled.append(prefix)

        time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
        reminders_text = "\n".join(f"  {s}" for s in scheduled)
        await update.message.reply_text(
            f"✅ Запомнил!\n\n📌 *{task}*\n📅 {time_str}\n\nНапомню:\n{reminders_text}",
            parse_mode="Markdown"
        )

    else:
        # Бессрочная задача — каждые 4 часа с 10:00 до 23:00
        job_name = f"periodic_{chat_id}_{task}"
        context.job_queue.run_repeating(
            send_periodic_reminder,
            interval=timedelta(hours=4),
            first=_next_periodic_time(now),
            data={"chat_id": chat_id, "task": task},
            name=job_name
        )
        await update.message.reply_text(
            f"🔁 Запомнил как *бессрочную задачу*!\n\n📌 *{task}*\n\n"
            f"Буду напоминать каждые *4 часа* с 10:00 до 23:00.",
            parse_mode="Markdown"
        )


def _next_periodic_time(now):
    """Следующее время срабатывания в промежутке 10:00–23:00"""
    tz = pytz.timezone(TIMEZONE)
    candidate = now + timedelta(minutes=1)
    if candidate.hour < 10:
        candidate = candidate.replace(hour=10, minute=0, second=0, microsecond=0)
    elif candidate.hour >= 23:
        candidate = (candidate + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return (candidate - now).total_seconds()


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

    # Отправляем только в промежутке 10:00–23:00
    if 10 <= now.hour < 23:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 *Напоминание!*\n\n📌 *{task}*",
            parse_mode="Markdown"
        )


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("📭 Активных напоминаний нет.")
        return

    seen = set()
    lines = [f"📋 *Активные напоминания ({BOT_NAME}):*\n"]
    for job in jobs:
        task = job.data.get("task", "")
        if task not in seen:
            seen.add(task)
            icon = "🔁" if job.name and job.name.startswith("periodic") else "📌"
            lines.append(f"{icon} {task}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stop_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.jobs()
    for job in jobs:
        job.schedule_removal()
    await update.message.reply_text("🛑 Все напоминания остановлены.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("stop", stop_all))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"{BOT_NAME} запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
