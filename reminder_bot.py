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
TIMEZONE = "Europe/Moscow"     # Поменяй на свою таймзону если нужно

logging.basicConfig(level=logging.INFO)

# Парсинг времени из текста
def parse_reminder(text: str):
    """
    Форматы:
    - "купить молоко через 30 минут"
    - "позвонить Ване завтра в 15:00"
    - "митинг 2024-04-01 10:30"
    - "сдать отчёт в 18:00"
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
        task = text[:m.start()].strip() + text[m.end():].strip()

    # завтра в HH:MM
    if not remind_at:
        m = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            remind_at = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
            task = text[:m.start()].strip() + text[m.end():].strip()

    # сегодня / в HH:MM
    if not remind_at:
        m = re.search(r'(?:сегодня\s+)?в\s+(\d{1,2}):(\d{2})', text, re.IGNORECASE)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            remind_at = candidate
            task = text[:m.start()].strip() + text[m.end():].strip()

    # YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM
    if not remind_at:
        m = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}:\d{2})', text)
        if m:
            date_str, time_str = m.group(1), m.group(2)
            fmt = "%Y-%m-%d" if "-" in date_str else "%d.%m.%Y"
            dt = datetime.strptime(f"{date_str} {time_str}", f"{fmt} %H:%M")
            remind_at = tz.localize(dt)
            task = text[:m.start()].strip() + text[m.end():].strip()

    task = task.strip(" ,.-")
    return task or "Напоминание", remind_at


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-напоминалка.\n\n"
        "Просто напиши мне задачу со временем, например:\n"
        "• купить молоко через 30 минут\n"
        "• позвонить Ване завтра в 15:00\n"
        "• сдать отчёт в 18:30\n"
        "• митинг 2025-04-01 10:00\n\n"
        "Я напомню тебе в нужное время! ⏰"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    task, remind_at = parse_reminder(text)

    if not remind_at:
        await update.message.reply_text(
            "🤔 Не смог найти время в сообщении.\n\n"
            "Попробуй так:\n"
            "• через 20 минут\n"
            "• в 15:00\n"
            "• завтра в 9:00\n"
            "• 2025-04-01 10:00"
        )
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    delay = (remind_at - now).total_seconds()

    if delay <= 0:
        await update.message.reply_text("⚠️ Это время уже прошло. Укажи время в будущем.")
        return

    chat_id = update.message.chat_id
    context.job_queue.run_once(
        send_reminder,
        when=delay,
        data={"chat_id": chat_id, "task": task},
        name=str(chat_id)
    )

    time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
    await update.message.reply_text(f"✅ Запомнил!\n\n📌 *{task}*\n⏰ Напомню {time_str}", parse_mode="Markdown")


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    task = job.data["task"]
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 *Напоминание!*\n\n{task}", parse_mode="Markdown")


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.jobs()
    if not jobs:
        await update.message.reply_text("📭 Активных напоминаний нет.")
        return

    lines = ["📋 *Активные напоминания:*\n"]
    for i, job in enumerate(jobs, 1):
        lines.append(f"{i}. {job.data['task']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
