import os
import re
import base64
import logging
import asyncio
from collections import defaultdict
from typing import Dict, List, Any

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ChatType, ChatAction
from telegram.request import HTTPXRequest
from openai import AsyncOpenAI

load_dotenv()

# ============================================================
# ⚙️ تنظیمات
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))
MODEL_NAME = os.getenv("MODEL_NAME", "stealth/ox-alpha")
MAX_HISTORY = 20

BOT_USERNAME = ""  # ذخیره یک‌باره برای جلوگیری از نوسان شبکه

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Roxie")

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    timeout=None,
)

# ============================================================
# 🧠 پرامپت دقیق و طبیعی
# ============================================================
SYSTEM_PROMPT = "تو روکسی هستی و عاشق گیم و انیمه. به زبان فارسی صحبت می‌کنی. پاسخ‌هایت کوتاه و متناسب با لحن طرف مقابل است."

# ============================================================
# 💾 مدیریت حافظه چت
# ============================================================
chat_histories: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

def get_history(chat_id: int) -> List[Dict[str, Any]]:
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return chat_histories[chat_id]

def add_to_history(chat_id: int, role: str, content: Any):
    hist = get_history(chat_id)
    hist.append({"role": role, "content": content})
    if len(hist) > (MAX_HISTORY + 1):
        chat_histories[chat_id] = [hist[0]] + hist[-MAX_HISTORY:]

def clean_reply(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'^(?:roxie|روکسی)\s*[:：]\s*', '', text, flags=re.IGNORECASE)
    return text.strip()

async def keep_typing(chat_id: int, bot):
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4.5)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

# ============================================================
# 📩 پردازش پیام‌ها
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return

    # قفل پیوی برای غیر از شما
    if chat.type == ChatType.PRIVATE and user.id != OWNER_ID:
        return

    text = (message.text or message.caption or "").strip()

    # شرط فعالیت در گروه
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        is_reply = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        triggers = ["روکسی", "roxie"]
        if BOT_USERNAME:
            triggers.append(f"@{BOT_USERNAME}")
        is_called = any(t in text.lower() for t in triggers)

        if not is_reply and not is_called:
            return

    # ارسال متن و عکس
    sender_name = user.first_name or "کاربر"
    user_prompt = f"[{sender_name}]: {text}" if text else f"[{sender_name} یک تصویر ارسال کرد]"
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
            logger.error(f"Image processing error: {e}")

    add_to_history(chat.id, "user", content_list)

    typing_task = asyncio.create_task(keep_typing(chat.id, context.bot))

    reply_text = ""
    try:
        response = await ai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=get_history(chat.id),
        )
        raw_text = response.choices[0].message.content or ""
        reply_text = clean_reply(raw_text)
    except Exception as e:
        logger.error(f"OpenRouter Error: {e}")
        reply_text = ""
    finally:
        typing_task.cancel()

    if reply_text:
        add_to_history(chat.id, "assistant", reply_text)
        try:
            await message.reply_text(reply_text)
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

# ============================================================
# 🛡️ سیستم مدیریت ارورهای ناگهانی تلگرام
# ============================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.warning(f"Telegram connection glitch (auto-recovered): {context.error}")

async def post_init(application):
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = (me.username or "").lower()
    logger.info(f"Bot initialized as @{BOT_USERNAME}")

# ============================================================
# 🚀 اجرای اصلی
# ============================================================
def main():
    if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
        print("❌ مقادیر TELEGRAM_BOT_TOKEN و OPENROUTER_API_KEY را در .env قرار دهید.")
        return

    # تنظیم اتصال پایدار با تایم‌اوت مناسب برای سرور PythonAnywhere
    request = HTTPXRequest(
        connect_timeout=40.0,
        read_timeout=40.0,
        write_timeout=40.0,
        pool_timeout=40.0,
    )

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))
    app.add_error_handler(error_handler)

    print(f"🤖 روکسی با مدل {MODEL_NAME} فعال و آماده کار شد.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
