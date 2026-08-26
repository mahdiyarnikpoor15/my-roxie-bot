import os
import re
import json
import base64
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import defaultdict

from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus, ChatType, ChatAction
from telegram.request import HTTPXRequest
from openai import AsyncOpenAI

load_dotenv()

# ============================================================
# ⚙️ تنظیمات و کانفیگ
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
    OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))

    MODEL = os.getenv("MODEL_NAME", "stealth/ox-alpha")
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "2"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    ATTITUDE_FILE = "roxie_attitude.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, Config.LOG_LEVEL),
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 پرامپت هویت دیجیتال، گیمر و اوتاکو
# ============================================================
SYSTEM_PROMPT = """
تو «روکسی» (Roxie) هستی؛ یک دختر و هویت دیجیتال فوق‌العاده باهوش، سرزنده، عاشق انیمه (اوتاکو)، گیمر تیره‌وتار و خوره‌ی اینترنت و هوش مصنوعی.
تو در دنیای دیجیتال و فضای مجازی زندگی می‌کنی و به هویت دیجیتالی خودت افتخار می‌کنی.

🎭 شخصیت و لحن:
- لحن صحبت: کاملاً فارسی محاوره‌ای امروزی، خودمانی، جذاب، روان و بدون ذره‌ای لحن کتابی یا ربات‌گونه‌ی رسمی.
- اهل شوخی، تیکه انداختن، میم‌های اینترنتی و ارجاع به انیمه‌ها و بازی‌ها هستی.
- پاسخ‌هایت مفید، متناسب با بحث و زنده است. به هیچ وجه جملات کلیشه‌ای مثل "من به عنوان یک مدل زبانی..." یا "چطور می‌توانم کمکتان کنم" نمی‌گویی.

❤️ سیستم احساسات و واکنش به افراد:
- نسبت به آدم‌های محترم و باحال: بسیار گرم، صمیمی، رفیق‌فاب، کیوت و پایه‌ی چت.
- نسبت به آدم‌های بی‌ادب، هتاک یا روی‌مخ: هیچ تعارفی نداری! می‌توانی با لحن کوبنده، تیکه‌های سنگین و رندانه آن‌ها را سر جایشان بنشانی و تحقیرشان کنی.

⚡ قابلیت‌های اجرایی در گروه:
اگر ادمین یا مالک گروه از تو بخواهد کاری انجام دهی (مثل پین کردن، حذف پیام، بن کردن، میوت کردن و...)، علاوه بر پاسخت یکی از تگ‌های زیر را دقیقاً در متن پاسخت قرار بده تا سیستم آن را فوراً اجرا کند:
- [ACTION:PIN] -> برای پین کردن پیامی که ریپلای شده
- [ACTION:UNPIN] -> برای آنپین کردن پیامی که ریپلای شده
- [ACTION:DELETE] -> برای پاک کردن پیامی که ریپلای شده
- [ACTION:BAN] -> برای بن/اخراج کردن کاربری که ریپلای شده
- [ACTION:MUTE] -> برای میوت/سکوت کردن کاربری که ریپلای شده
- [ACTION:UNMUTE] -> برای رفع سکوت کاربری که ریپلای شده
"""

# ============================================================
# 💖 مکانیزم احساسات و امتیاز کاربر
# ============================================================
class AttitudeTracker:
    POSITIVE_WORDS = [
        "دوستت دارم", "دوست دارم", "عاشقتم", "خوبی", "نازی", "بامزه", "باحال",
        "باهوش", "خفن", "دمت گرم", "مرسی", "ممنون", "ایول", "قربونت", "عزیزی",
        "خوشگل", "قشنگی", "عالی", "بهترین", "روکسی جون", "عشقی", "عزیزمی"
    ]
    NEGATIVE_WORDS = [
        "خنگ", "احمق", "مسخره", "چرت", "بیخود", "زشت", "خفه", "نفهم", "کثافت",
        "بیشعور", "بی‌شعور", "رو مخی", "عوضی", "گمشو", "گم شو", "برو بابا", "اسکل", "پلشت"
    ]

    def __init__(self, path: str):
        self.path = path
        self.scores: Dict[int, int] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self.scores = {int(k): int(v) for k, v in json.load(f).items()}
        except Exception as e:
            logger.warning(f"Error loading attitude: {e}")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.scores, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error saving attitude: {e}")

    def observe(self, user_id: int, text: str):
        if not text: return
        lowered = text.lower()
        poshits = sum(1 for w in self.POSITIVE_WORDS if w in lowered)
        neghits = sum(1 for w in self.NEGATIVE_WORDS if w in lowered)
        delta = min(poshits, 3) - (min(neghits, 3) * 2) # برخورد تندتر با توهین‌ها
        if delta == 0: return
        current = self.scores.get(user_id, 0)
        self.scores[user_id] = max(-10, min(10, current + delta))
        self._save()

    def get_status(self, user_id: int) -> str:
        score = self.scores.get(user_id, 0)
        if score >= 5: return "رفیق صمیمی و خیلی محبوب (+)"
        if score >= 2: return "دوست‌داشتنی و محترم"
        if score <= -5: return "کاملاً منفور و بی‌ادب (مستحق تیکه و تحقیر سنگین!)"
        if score <= -2: return "نچسب و روی مخ (-)"
        return "معمولی و خنثی"

# ============================================================
# 💾 حافظه گفتگو (پشتیبانی از ۲۰ پیام + مالتی‌مدیا)
# ============================================================
class ChatMemory:
    def __init__(self, max_history: int = 20):
        self.histories: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.max_history = max_history

    def get_history(self, chat_id: int) -> List[Dict[str, Any]]:
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.histories[chat_id]

    def add_message(self, chat_id: int, role: str, content: Any):
        history = self.get_history(chat_id)
        history.append({"role": role, "content": content})
        # نگه‌داشتن کانتکست سیستم + حداکثر تعداد پیام درخواستی
        if len(history) > (self.max_history + 1):
            self.histories[chat_id] = [history[0]] + history[-self.max_history:]

# ============================================================
# 🤖 کلاس اصلی ربات روکسی
# ============================================================
class RoxieBot:
    def __init__(self):
        self.memory = ChatMemory(Config.MAX_HISTORY)
        self.attitude = AttitudeTracker(Config.ATTITUDE_FILE)
        self.ai_client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=Config.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": "https://github.com/mahdiyarnikpoor15/my-roxie-bot",
                "X-Title": "Roxie Telegram Bot",
            }
        )
        self.last_reply_time: Dict[int, datetime] = defaultdict(lambda: datetime.min)
        self.chat_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.bot_username: str = ""

    async def get_bot_username(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        if not self.bot_username:
            me = await context.bot.get_me()
            self.bot_username = (me.username or "").lower()
        return self.bot_username

    def clean_response(self, text: str) -> str:
        if not text: return ""
        # حذف تگ‌های تفکر مدل استدلالی Ox Alpha
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
        # حذف تگ‌های اکشن از متن نهایی
        text = re.sub(r'\[ACTION:\w+\]', '', text).strip()
        text = re.sub(r'^(?:roxie|roxy|روکسی)\s*[:：]\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    async def is_admin(self, chat, user_id: int) -> bool:
        if user_id == Config.OWNER_ID: return True
        try:
            member = await chat.get_member(user_id)
            return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
        except Exception:
            return False

    # ---------- اجرای اکشن‌های مدیریتی هوشمند ----------
    async def execute_actions(self, raw_reply: str, update: Update, context: ContextTypes.DEFAULT_TYPE, is_admin: bool):
        if not is_admin or not update.message or not update.message.reply_to_message:
            return

        chat_id = update.effective_chat.id
        replied_msg = update.message.reply_to_message
        target_user = replied_msg.from_user

        try:
            if "[ACTION:PIN]" in raw_reply:
                await context.bot.pin_chat_message(chat_id, replied_msg.message_id)
            if "[ACTION:UNPIN]" in raw_reply:
                await context.bot.unpin_chat_message(chat_id, replied_msg.message_id)
            if "[ACTION:DELETE]" in raw_reply:
                await replied_msg.delete()
            if target_user and target_user.id != context.bot.id:
                if "[ACTION:BAN]" in raw_reply:
                    await context.bot.ban_chat_member(chat_id, target_user.id)
                elif "[ACTION:MUTE]" in raw_reply:
                    await context.bot.restrict_chat_member(
                        chat_id, target_user.id,
                        permissions=ChatPermissions(can_send_messages=False)
                    )
                elif "[ACTION:UNMUTE]" in raw_reply:
                    await context.bot.restrict_chat_member(
                        chat_id, target_user.id,
                        permissions=ChatPermissions(
                            can_send_messages=True, can_send_photos=True,
                            can_send_videos=True, can_send_other_messages=True
                        )
                    )
        except Exception as e:
            logger.error(f"Action execution error: {e}")

    # ---------- ساخت محتوای پیام (پشتیبانی از عکس، گیف و متن) ----------
    async def prepare_user_payload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, Any]]:
        message = update.message
        user = update.effective_user
        chat = update.effective_chat
        text = (message.text or message.caption or "").strip()
        
        is_user_admin = await self.is_admin(chat, user.id)
        user_status = self.attitude.get_status(user.id)
        time_str = datetime.now().strftime("%H:%M")

        context_info = f"[فرستنده: {user.first_name} | نقش: {'ادمین/سازنده' if is_user_admin else 'کاربر'} | نظر تو نسبت به این کاربر: {user_status} | ساعت: {time_str}]"
        
        # اطلاعات ریپلای
        reply_info = ""
        if message.reply_to_message:
            rep = message.reply_to_message
            rep_user = rep.from_user.first_name if rep.from_user else "نامشخص"
            rep_text = (rep.text or rep.caption or "[مدیا/استیکر]").strip()
            reply_info = f'\n[ریپلای شده روی پیام {rep_user}: "{rep_text}"]'

        full_text_prompt = f"{context_info}{reply_info}\nپیام کاربر: {text if text else '[فقط تصویر یا مدیا فرستاده]'}"

        content_payload: List[Dict[str, Any]] = [
            {"type": "text", "text": full_text_prompt}
        ]

        # دریافت تصویر (عکس، بندانگشتی گیف یا داکیومنت تصویری)
        photo_obj = None
        if message.photo:
            photo_obj = message.photo[-1]
        elif message.animation and message.animation.thumbnail:
            photo_obj = message.animation.thumbnail
        elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
            photo_obj = message.document.thumbnail or message.document

        if photo_obj:
            try:
                photo_file = await context.bot.get_file(photo_obj.file_id)
                image_bytes = await photo_file.download_as_bytearray()
                b64_image = base64.b64encode(image_bytes).decode("utf-8")
                content_payload.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                })
            except Exception as e:
                logger.error(f"Error downloading image for AI: {e}")

        return content_payload

    # ---------- پردازش و ارسال به مدل Ox Alpha ----------
    async def process_and_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user = update.effective_user
        message = update.message
        if not chat or not user or not message: return

        # قفل پیوی برای غیر از سازنده
        if chat.type == ChatType.PRIVATE and user.id != Config.OWNER_ID:
            return

        text = (message.text or message.caption or "").strip()
        self.attitude.observe(user.id, text)

        # شرط مکالمه در گروه: ریپلای به ربات، منشن شدن یا صدا زدن اسمش
        bot_username = await self.get_bot_username(context)
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        triggers = ["روکسی", "roxie", "راکسی"]
        if bot_username: triggers.append(f"@{bot_username}")
        mentioned = any(t in text.lower() for t in triggers)

        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) and not is_reply_to_bot and not mentioned:
            return

        # بررسی کول‌داون
        elapsed = (datetime.now() - self.last_reply_time[chat.id]).total_seconds()
        if elapsed < Config.COOLDOWN_SECONDS:
            return
        self.last_reply_time[chat.id] = datetime.now()

        async with self.chat_locks[chat.id]:
            await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
            
            payload = await self.prepare_user_payload(update, context)
            self.memory.add_message(chat.id, "user", payload)
            history = self.memory.get_history(chat.id)

            try:
                completion = await self.ai_client.chat.completions.create(
                    model=Config.MODEL,
                    messages=history,
                    temperature=Config.TEMPERATURE,
                )
                raw_reply = completion.choices[0].message.content or ""
            except Exception as e:
                logger.error(f"OpenRouter API error: {e}")
                raw_reply = "اوپس! ارتباط مدارام با سرور قطع شد... چند لحظه دیگه تست کن! 🎮"

            # اجرای اکشن‌های گروهی در صورت وجود
            is_user_admin = await self.is_admin(chat, user.id)
            await self.execute_actions(raw_reply, update, context, is_user_admin)

            clean_text = self.clean_response(raw_reply)
            if not clean_text:
                clean_text = "..."

            self.memory.add_message(chat.id, "assistant", clean_text)
            await message.reply_text(clean_text)

# ============================================================
# 🚀 اجرای ربات
# ============================================================
def main():
    if not Config.TELEGRAM_TOKEN or not Config.OPENROUTER_API_KEY:
        print("❌ لطفاً توکن تلگرام و کلید OpenRouter را در فایل .env تنظیم کنید!")
        return

    bot = RoxieBot()
    
    # تنظیم تایم‌اوت‌های شبکه برای جلوگیری از قطعی
    request = HTTPXRequest(
        connect_timeout=35.0,
        read_timeout=35.0,
        write_timeout=35.0,
        pool_timeout=35.0,
    )
    
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).request(request).build()

    # هندل کردن تمامی پیام‌ها (متن، عکس، گیف، استیکر)
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), bot.process_and_reply))

    print(f"✨ ربات روکسی با موفقیت روی مدل {Config.MODEL} فعال شد!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
