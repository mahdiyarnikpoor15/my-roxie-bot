import os
import re
import base64
import logging
from collections import defaultdict
from typing import Dict, List, Any

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ChatType, ChatAction
from telegram.request import HTTPXRequest
from openai import AsyncOpenAI

load_dotenv()

# تنظیمات
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))
MODEL_NAME = os.getenv("MODEL_NAME", "stealth/ox-alpha")
MAX_HISTORY = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Roxie")

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    timeout=90.0,
    max_retries=2,
)

SYSTEM_PROMPT = """
تو «روکسی» هستی؛ یک دختر دیجیتال باهوش، سرزنده، عاشق گیم و انیمه.
به زبان فارسی محاوره‌ای، کاملاً طبیعی، روان و امروزی چت می‌کنی.
پاسخ‌هایت کوتاه، صمیمی، متناسب با لحن طرف مقابل و بدون ادبیات کتابی یا ربات‌گونه است.
"""

chat_histories: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

def get_history(chat_id: int) -> List[Dict[str, Any]]:
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT.strip()}]
    return chat_histories[chat_id]

def add_to_history(chat_id: int, role: str, content: Any):
    hist = get_history(chat_id)
    hist.append({"role": role, "content": content})
    if len(hist) > (MAX_HISTORY + 1):
        chat_histories[chat_id] = [hist[0]] + hist[-MAX_HISTORY:]

def clean_reply(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'^(?:roxie|روکسی)\s*[:：]\s*', '', text, flags=re.IGNORECASE)
    return text.strip()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat: return

    if chat.type == ChatType.PRIVATE and user.id != OWNER_ID:
        return

    text = (message.text or message.caption or "").strip()

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        bot_user = await context.bot.get_me()
        is_reply = bool(message.reply_to_message and message.reply_to_message.from_user.id == bot_user.id)
        triggers = ["روکسی", "roxie", f"@{bot_user.username.lower()}"] if bot_user.username else ["روکسی", "roxie"]
        is_called = any(t in text.lower() for t in triggers)
        if not is_reply and not is_called:
            return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)

    sender_name = user.first_name or "کاربر"
    user_prompt = f"[{sender_name}]: {text}" if text else f"[{sender_name} یک تصویر فرستاد]"
    content_list: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]

    photo_obj = None
    if message.photo:
        photo_obj = message.photo[-1]
    elif message.animation and message.animation.thumbnail:
        photo_obj = message.animation.thumbnail

    if photo_obj:
        try:
            file = await context.bot.get_file(photo_obj.file_id)
            img_bytes = await file.download_as_bytearray()
            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            content_list.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            })
        except Exception as e:
            logger.error(f"Image download error: {e}")

    add_to_history(chat.id, "user", content_list)

    try:
        response = await ai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=get_history(chat.id),
        )
        raw_text = response.choices[0].message.content or ""
        reply_text = clean_reply(raw_text)
    except Exception as e:
        logger.error(f"AI Error: {e}")
        reply_text = "یه لحظه لگ زدم، دوباره بگو! 🎮"

    if reply_text:
        add_to_history(chat.id, "assistant", reply_text)
        await message.reply_text(reply_text)

def main():
    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
    )
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    print(f"✨ روکسی با مدل {MODEL_NAME} آماده است!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
