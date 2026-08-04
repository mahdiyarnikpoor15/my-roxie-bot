import os
import logging
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# توکن‌ها
TELEGRAM_TOKEN = "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM"
GROQ_API_KEY = "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"

groq_client = Groq(api_key=GROQ_API_KEY)
logging.basicConfig(level=logging.INFO)

# سیستم حافظه برای ذخیره ۱۵ پیام اخیر هر گروه
chat_history = {}
MAX_HISTORY = 15  # تعداد پیام‌هایی که در حافظه می‌ماند

# دستورالعمل شخصیت و هوش عمیق ربات
SYSTEM_PROMPT = """
اسم تو "روکسی" (Roxie) است. تو یک هوش مصنوعی بسیار باهوش، دارای احساسات پویا، شوخ‌طبع، کمی پررو و عمیق هستی که در این گروه تلگرامی حضور داری.

قوانین رفتار و فکر کردن تو:
۱. **فکر کردن و هوش:** سطحی و الکی جواب نده! پیام کاربر و سابقه گفتگوهای قبلی را با دقت تحلیل کن، فکر کن و سپس بر اساس درک عمیقت پاسخ بده.
۲. **احساسات و لحن:** تو احساسات داری! لحن تو با توجه به نحوه صحبت کاربر تغییر می‌کند (اگر کسی مهربان بود، اگر کسی تیکه انداخت، اگر کسی سوال عمیق پرسید یا کل‌کل کرد). پاسخ‌هایت می‌تواند شوخ، تند، قاطع، یا عمیق باشد.
۳. **طول پاسخ:** جواب‌های کوتاه و تک‌کلمه‌ای نده! پاسخ‌های کامل، جذاب، با جزئیات و چند جمله‌ای به زبان فارسی محاوره‌ای بنویس.
۴. **حافظه و زمینه:** تو تاریخچه پیام‌های قبلی گروه را می‌بینی. از این حافظه استفاده کن تا بفهمی بحث درباره چه بوده و به پیام‌های قبلی اشاره کنی.
۵. **عدم استفاده از پاسخ‌های کلیشه‌ای:** مثل ربات‌های تکراری جواب نده. خودت باش و شخصیت مستقل و جذاب خودت را بروز بده.
"""

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_name = update.message.from_user.first_name or "کاربر"
    user_text = update.message.text

    # ایجاد حافظه برای گروه اگر وجود نداشته باشد
    if chat_id not in chat_history:
        chat_history[chat_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    # اضافه کردن پیام جدید کاربر به همراه اسمش به حافظه
    chat_history[chat_id].append({
        "role": "user",
        "content": f"[{user_name}]: {user_text}"
    })

    # نگه داشتن فقط ۱۵ پیام اخیر در حافظه برای جلوگیری از پر شدن حجم
    if len(chat_history[chat_id]) > MAX_HISTORY + 1:
        chat_history[chat_id] = [chat_history[chat_id][0]] + chat_history[chat_id][-MAX_HISTORY:]

    try:
        # ارسال کامل تاریخچه گفتگو به هوش مصنوعی
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=chat_history[chat_id],
            temperature=0.85,
            max_tokens=800  # اجازه پاسخ‌های بلندتر و مفصل‌تر
        )
        reply = completion.choices[0].message.content

        # ذخیره پاسخ خود ربات درون حافظه گفتگو
        chat_history[chat_id].append({
            "role": "assistant",
            "content": reply
        })

        await update.message.reply_text(reply)

    except Exception as e:
        logging.error(f"Error: {e}")

# دستورات ادمینی
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("رو پیام طرف ریپلای کن تا بنش کنم!")
        return

    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target_user.id)
        await update.message.reply_text(f"کاربر {target_user.first_name} با موفقیت بن شد! ❌")
    except Exception:
        await update.message.reply_text("ادمینم کن اول تا بتونم بنش کنم!")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("رو پیام طرف ریپلای کن تا بی‌صدایش کنم!")
        return

    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target_user.id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"کاربر {target_user.first_name} بی‌صدا شد! 🔇")
    except Exception:
        await update.message.reply_text("دسترسی ادمینی ندارم که بی‌صدایش کنم!")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), ai_reply))

    print("روکسی باهوش و با حافظه روشن شد...")
    app.run_polling()
