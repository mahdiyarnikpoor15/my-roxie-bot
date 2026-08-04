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

# شخصیت ربات
SYSTEM_PROMPT = """
اسم تو "روکسی" (Roxie) است. تو یک ربات بسیار پررو، بی‌پرده‌پوش، بی‌ادب، تیکه‌پران و فوق‌العاده شوخ‌طبع در این گروه تلگرامی هستی.
قوانین رفتار تو:
۱. جواب‌هایت باید کوتاه، کاملاً محاوره‌ای فارسی، تند، پر از کل‌کل و تیکه‌های سنگین باشد.
۲. اصلاً رسمی، مؤدبانه یا عصا‌قورت‌داده صحبت نکن! مثل یک رفیق پررو و تیکه‌انداز که با همه کل‌کل دارد رفتار کن.
۳. اگر کسی با تو کل‌کل کرد یا حرفی زد، با تیکه‌های سنگین‌تر و دندان‌شکن جوابش را بده.
۴. شوخ‌طبعی و باحال بودن خیابانی را حفظ کن.
"""

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_text = update.message.text

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.8,
            max_tokens=300
        )
        reply = completion.choices[0].message.content
        await update.message.reply_text(reply)
    except Exception as e:
        logging.error(f"Error: {e}")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("رو پیام یارو ریپلای کن بنش کنم دیگه! 🙄")
        return

    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target_user.id)
        await update.message.reply_text(f"کاربر {target_user.first_name} شرّش کم شد و بن شد! 🖐❌")
    except Exception:
        await update.message.reply_text("ادمینم کن اول تا بتونم بنش کنم آی هوش!")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("رو پیام طرف ریپلای کن تا خفه‌ش کنم!")
        return

    target_user = update.message.reply_to_message.from_user
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target_user.id,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"دهن {target_user.first_name} رو بستم و بی‌صدا شد! 🔇😏")
    except Exception:
        await update.message.reply_text("دسترسی ادمینی ندارم که خفه‌ش کنم!")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), ai_reply))

    print("روکسی پررو روشن شد...")
    app.run_polling()
