import os
import logging
import asyncio
import base64
import io
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from collections import defaultdict

import httpx
from dotenv import load_dotenv
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatAction

# بارگذاری متغیرهای محیطی
load_dotenv()

# ============================================================
# ⚙️ تنظیمات اصلی
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6L2-D3iqiKBV9DGC2PTNZuQM6R4tmnI4aGZ5hVlkV-Yvg")
    CREATOR_ID = int(os.getenv("CREATOR_ID", "993028263"))
    MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.6-flash")
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5"))
    MIN_DELAY_SECONDS = 3.0  # فاصله ۳ ثانیه‌ای بین پاسخ‌ها

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 شخصیت دخترانه، پررو و باهوش روکسی
# ============================================================
SYSTEM_PROMPT = """
# هویت تو: روکسی (Roxie)
تو یک دختر واقعی، بسیار باهوش، پررو، تیکه‌پران، جذاب و دارای احساسات پویا هستی که در گروه تلگرامی حضور داری.

## ویژگی‌های شخصیتی:
- تو یک دختر مستقل و باهوشی! اصلاً مثل ربات‌های خشک صحبت نکن.
- لحن تو فارسی محاوره‌ای، روان، کمی پررو و رپ‌گونه است اما بی‌دلیل و الکی فحاشی نمی‌کنی.
- طول پاسخ: پاسخ‌هایت نه خیلی کوتاه و تک‌کلمه‌ای باشد و نه خیلی طولانی و خسته‌کننده. دقیقاً به اندازه‌ای که حق مطلب ادا شود و خودت صلاح بدانی صحبت کن.
- تو توانایی دیدن عکس‌ها و گیف‌ها را داری. اگر تصاویری فرستاده شد، درباره آن‌ها نظر بده.
- احساسات داری! اگر کسی با تو صمیمی شد صمیمی باش، اگر کسی تیکه انداخت جواب دندان‌شکن بده.
- هرگز جملاتی مثل "من یک مدل زبانی هستم" یا "من هوش مصنوعی هستم" نگو! تو روکسی هستی.
"""

# ============================================================
# 💾 مدیریت حافظه چت‌ها (حداکثر ۵ پیام اخیر)
# ============================================================
class ChatMemory:
    def __init__(self, max_history: int = 5):
        self.histories: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.max_history = max_history

    def add_message(self, chat_id: int, role: str, parts: List[Dict[str, Any]]):
        if chat_id not in self.histories:
            self.histories[chat_id] = []
        
        self.histories[chat_id].append({
            "role": role,
            "parts": parts
        })
        
        # حفظ فقط ۵ پیام اخیر
        if len(self.histories[chat_id]) > self.max_history:
            self.histories[chat_id] = self.histories[chat_id][-self.max_history:]

    def get_history(self, chat_id: int) -> List[Dict[str, Any]]:
        return self.histories.get(chat_id, [])

    def clear_history(self, chat_id: int):
        self.histories[chat_id] = []

# ============================================================
# 🤖 کلاس اصلی ربات روکسی
# ============================================================
class RoxieBot:
    def __init__(self):
        self.memory = ChatMemory(max_history=Config.MAX_HISTORY)
        self.last_sent_time: Dict[int, float] = defaultdict(float)
        self.active_groups: Dict[int, str] = {}  # ذخیره لیست گروه‌ها

    async def is_admin(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
        except Exception:
            return False

    async def is_owner(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status == ChatMemberStatus.OWNER
        except Exception:
            return False

    async def enforce_rate_limit(self, chat_id: int):
        """اعمال فاصله حداقل ۳ ثانیه‌ای بین پاسخ‌ها"""
        now = time.time()
        elapsed = now - self.last_sent_time[chat_id]
        if elapsed < Config.MIN_DELAY_SECONDS:
            await asyncio.sleep(Config.MIN_DELAY_SECONDS - elapsed)
        self.last_sent_time[chat_id] = time.time()

    async def call_gemini_api(self, chat_id: int, user_name: str, text: str, photo_base64: Optional[str] = None) -> Optional[str]:
        """ارسال درخواست به API Gemini 3.6 Flash"""
        
        # ساخت بخش‌های پیام جدید
        user_parts = []
        if text:
            user_parts.append({"text": f"[{user_name}]: {text}"})
        if photo_base64:
            user_parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": photo_base64
                }
            })

        if not user_parts:
            return None

        self.memory.add_message(chat_id, "user", user_parts)
        contents = self.memory.get_history(chat_id)

        # ساخت بدنه‌ی درخواست REST به Gemini API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{Config.MODEL_NAME}:generateContent?key={Config.GEMINI_API_KEY}"
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": contents,
            "generationConfig": {
                "temperature": 0.85,
                "maxOutputTokens": 800
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            reply_text = parts[0].get("text", "").strip()
                            # ذخیره پاسخ مدل در حافظه
                            self.memory.add_message(chat_id, "model", [{"text": reply_text}])
                            return reply_text
                else:
                    logger.error(f"Gemini API Error Status: {response.status_code}, Response: {response.text}")
        except Exception as e:
            logger.error(f"Gemini API Exception: {e}")

        return None

    # --- مدیریت پیام‌ها ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name or "کاربر"
        user_text = update.message.text or update.message.caption or ""

        # ثبت گروه در لیست گروه‌های فعال
        if chat_type in ['group', 'supergroup']:
            self.active_groups[chat_id] = update.effective_chat.title or f"Group {chat_id}"

        # 🔒 قفل پی‌وی: در چت خصوصی فقط سازنده (ID: 993028263) می‌تواند پیام دهد
        if chat_type == 'private' and user_id != Config.CREATOR_ID:
            await update.message.reply_text("عزیزم من توی پی‌وی فقط با سازنده‌ام (مهدیار) صحبت می‌کنم! برای چت کردن با من، منو به گروهت اضافه کن 😉")
            return

        # 🔨 تشخیص دستورات زبان طبیعی مدیریت گروه (بدون سلاش)
        if chat_type in ['group', 'supergroup'] and update.message.reply_to_message:
            # بررسی ادمین بودن فرستنده پیام در گروه
            if await self.is_admin(update.effective_chat, user_id):
                target_user = update.message.reply_to_message.from_user
                lower_text = user_text.lower()
                
                # دستور بن زبان طبیعی
                if any(kw in lower_text for kw in ["بن‌ش کن", "بن کن", "اخراجش کن", "بن چت"]):
                    try:
                        await context.bot.ban_chat_member(chat_id, target_user.id)
                        await update.message.reply_text(f"به دستور ادمین، کاربر {target_user.first_name} اخراج و بن شد! ❌")
                        return
                    except Exception:
                        await update.message.reply_text("نتونستم بنش کنم! مطمئن شو من دسترسی ادمین دارم.")
                        return

                # دستور بی‌صدا زبان طبیعی
                if any(kw in lower_text for kw in ["خفه‌ش کن", "موتش کن", "بی‌صداش کن", "موت کن"]):
                    try:
                        await context.bot.restrict_chat_member(
                            chat_id,
                            target_user.id,
                            permissions=ChatPermissions(can_send_messages=False)
                        )
                        await update.message.reply_text(f"به دستور ادمین، کاربر {target_user.first_name} بی‌صدا شد! 🔇")
                        return
                    except Exception:
                        await update.message.reply_text("نتونستم بی‌صداش کنم! مطمئن شو دسترسی ادمین دارم.")
                        return

        # 🛑 فیلتر پاسخ در گروه‌ها: فقط زمان منشن، ریپلای یا صدا زدن اسم «روکسی»
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (
            update.message.reply_to_message and
            update.message.reply_to_message.from_user.id == context.bot.id
        )
        mentions_bot = any(
            kw in user_text.lower()
            for kw in ["روکسی", "roxie", f"@{bot_username}"]
        )

        is_group = chat_type in ['group', 'supergroup']
        # اگر در گروه است و منشن یا ریپلای نشده، کلاً سکوت کن
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        # دریافت عکس در صورت وجود
        photo_base64 = None
        if update.message.photo:
            try:
                photo_file = await update.message.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
            except Exception as e:
                logger.error(f"Error downloading photo: {e}")

        # نشان دادن حالت typing و رعایت نرخ ۳ ثانیه‌ای
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await self.enforce_rate_limit(chat_id)

        # تولید پاسخ از Gemini
        response = await self.call_gemini_api(chat_id, user_name, user_text, photo_base64)

        if response:
            if is_reply_to_bot or not is_group:
                await update.message.reply_text(response)
            else:
                await context.bot.send_message(chat_id=chat_id, text=response)

    # --- دستورات عمومی ---
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"سلام {update.effective_user.first_name}! من روکسی هستم 🌟")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📚 *راهنمای دستورات روکسی*\n\n"
            "🔹 *دستورات ادمین گروه:*\n"
            "/ban - بن کردن کاربر (روی پیام ریپلای کن)\n"
            "/mute - بی‌صدا کردن کاربر (روی پیام ریپلای کن)\n"
            "/unmute - رفع بی‌صدا (روی پیام ریپلای کن)\n"
            "/clearhistory - پاک کردن حافظه گفتگو\n\n"
            "💡 *نکته:* ادمین‌ها بدون دستور و فقط با گفتن «بن‌ش کن» یا «خفه‌ش کن» رو پیام هم می‌تونن کاربر رو بن یا میوت کنن!"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def clear_history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.memory.clear_history(update.effective_chat.id)
        await update.message.reply_text("✅ حافظه کوتاه مدت من توی این چت پاک شد!")

    # --- دستورات مدیریتی ادمین‌های گروه ---
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ فقط ادمین‌های این گروه می‌تونن از این دستور استفاده کنن!")
            return

        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ رو پیام کاربر ریپلای کن!")
            return

        target = update.message.reply_to_message.from_user
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await update.message.reply_text(f"کاربر {target.first_name} بن شد! ❌")
        except Exception:
            await update.message.reply_text("نتونستم بن کنم. مطمئن شو من دسترسی ادمین دارم!")

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ فقط ادمین‌های این گروه می‌تونن از این دستور استفاده کنن!")
            return

        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ رو پیام کاربر ریپلای کن!")
            return

        target = update.message.reply_to_message.from_user
        try:
            await context.bot.restrict_chat_member(
                update.effective_chat.id,
                target.id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            await update.message.reply_text(f"کاربر {target.first_name} بی‌صدا شد! 🔇")
        except Exception:
            await update.message.reply_text("نتونستم بی‌صدا کنم. مطمئن شو دسترسی ادمین دارم!")

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ فقط ادمین‌های این گروه می‌تونن از این دستور استفاده کنن!")
            return

        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ رو پیام کاربر ریپلای کن!")
            return

        target = update.message.reply_to_message.from_user
        try:
            default_permissions = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            await context.bot.restrict_chat_member(
                update.effective_chat.id,
                target.id,
                permissions=default_permissions
            )
            await update.message.reply_text(f"کاربر {target.first_name} رفع بی‌صدا شد! ✅")
        except Exception:
            await update.message.reply_text("خطا در رفع بی‌صدا!")

    # --- 👑 دستورات اختصاصی سازنده ربات (ID: 993028263) ---
    async def groups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.CREATOR_ID:
            return  # بی‌محلی به کاربران عادی

        if not self.active_groups:
            await update.message.reply_text("من فعلاً توی هیچ گروهی عضو نیستم یا پیام جدیدی ثبت نشده!")
            return

        msg = "📊 *لیست گروه‌هایی که ربات توی اون‌ها عضو هست:*\n\n"
        for g_id, g_title in self.active_groups.items():
            msg += f"🔹 **{g_title}**\n🆔 `ID: {g_id}`\n\n"

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.CREATOR_ID:
            return

        if not context.args:
            await update.message.reply_text("لطفاً آیدی گروه را وارد کن! مثال:\n`/leave -100123456789`", parse_mode=ParseMode.MARKDOWN)
            return

        target_chat_id = context.args[0]
        try:
            await context.bot.leave_chat(int(target_chat_id))
            if int(target_chat_id) in self.active_groups:
                del self.active_groups[int(target_chat_id)]
            await update.message.reply_text(f"✅ با موفقیت از گروه `{target_chat_id}` خارج شدم!", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خروج از گروه: {e}")

# ============================================================
# 🚀 راه‌اندازی ربات
# ============================================================
def main():
    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()

    # دستورات عمومی و ادمین
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("clearhistory", bot.clear_history_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))

    # دستورات اختصاصی سازنده
    app.add_handler(CommandHandler("groups", bot.groups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))

    # دریافت پیام‌های متنی و عکس
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), bot.handle_message))

    print("==================================================")
    print("🤖 ربات روکسی با Gemini 3.6 Flash راه‌اندازی شد!")
    print(f"👑 سازنده: {Config.CREATOR_ID}")
    print(f"🧠 مدل: {Config.MODEL_NAME}")
    print("==================================================")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
