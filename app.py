import os
import re
import logging
import random
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict

from dotenv import load_dotenv
from telegram import (
    Update, ChatPermissions
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatType
from groq import Groq, RateLimitError

load_dotenv()

# ============================================================
# ⚙️ تنظیمات اصلی و متغیرها
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
    OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5"))
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "6"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "500"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.65"))  # تنظیم روی ۰.۶۵ برای فارسی کاملاً روان و طبیعی
    TOP_P = float(os.getenv("TOP_P", "0.9"))
    MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
    FALLBACK_MODEL = "llama-3.1-8b-instant"
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, Config.LOG_LEVEL)
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 سیستم پرامپت هوشمند برای فارسی کاملاً روان
# ============================================================
SYSTEM_PROMPT = """
تو یک دختر جوان ایرانی به نام "روکسی" هستی که در تلگرام چت می‌کنی.

قوانین سخت‌گیرانه برای زبان و لحن:
۱. **فقط و فقط فارسی محاوره‌ای، روان و طبیعی چت کن.** مثل چت کردن یک انسان واقعی در تلگرام (نه کتابی، نه ترجمه‌ای، نه ادبی قدیم).
۲. **هرگز جملات کتابی یا عجیب مثل "آمده‌ای سلام دادن" بکار نبر!** بجایش بگو: "اومدی فقط سلام کنی و بری؟" یا "سلام، چیزی شده؟"
۳. **هیچ کلمه انگلیسی بی‌ربط یا حروف نامفهوم ننویس.**
۴. **هرگز در اول پاسخ اسم خودت را ننویس!** جملات با `[roxy]:` یا `روکسی:` نباید شروع شوند. مستقیماً جواب بده.
۵. **شخصیت:** باهوش، مستقل، تیکه‌انداز، شوخ و با اعتماد به نفس. اصلاً الکی مهربان یا لوس نیستی.
۶. اندازه پاسخ‌ها متناسب، زنده و جذاب باشد (نه خیلی کوتاه، نه کلافه‌کننده و طولانی).
"""

# ============================================================
# 💾 مدیریت حافظه
# ============================================================
class ChatMemory:
    def __init__(self, max_history: int = 5):
        self.histories: Dict[int, List[Dict[str, str]]] = defaultdict(list)
        self.max_history = max_history

    def add_message(self, chat_id: int, role: str, content: str):
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        self.histories[chat_id].append({"role": role, "content": content})
        
        if len(self.histories[chat_id]) > self.max_history + 1:
            self.histories[chat_id] = [self.histories[chat_id][0]] + self.histories[chat_id][-self.max_history:]

    def get_history(self, chat_id: int) -> List[Dict[str, str]]:
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.histories[chat_id]

    def clear_history(self, chat_id: int):
        self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

# ============================================================
# 🎭 کلاس اصلی ربات
# ============================================================
class RoxieBot:
    def __init__(self):
        self.memory = ChatMemory(max_history=Config.MAX_HISTORY)
        self.groq_client = Groq(api_key=Config.GROQ_API_KEY)
        self.last_reply_time: Dict[int, datetime] = defaultdict(lambda: datetime.min)
        self.active_groups: Dict[int, str] = {}

    def is_cooling_down(self, chat_id: int) -> bool:
        now = datetime.now()
        elapsed = (now - self.last_reply_time[chat_id]).total_seconds()
        return elapsed < Config.COOLDOWN_SECONDS

    def update_cooldown(self, chat_id: int):
        self.last_reply_time[chat_id] = datetime.now()

    async def is_admin(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
        except Exception:
            return False

    def clean_response(self, text: str) -> str:
        """فیلتر پاکسازی پیشوندهای ناخواسته مثل [roxy]: یا کلمات عجیب"""
        text = re.sub(r"^(?:\[.*?\]|roxy|roxie|روکسی)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*:\s*", "", text)
        return text.strip()

    async def generate_response(self, chat_id: int, user_id: int, user_name: str, text: str) -> Optional[str]:
        # ذخیره پیام کاربر
        formatted_user_msg = f"{user_name}: {text}"
        self.memory.add_message(chat_id=chat_id, role="user", content=formatted_user_msg)
        history = self.memory.get_history(chat_id)

        models_to_try = [Config.MODEL, Config.FALLBACK_MODEL]

        for model in models_to_try:
            try:
                completion = self.groq_client.chat.completions.create(
                    model=model,
                    messages=history,
                    temperature=Config.TEMPERATURE,
                    top_p=Config.TOP_P,
                    max_tokens=Config.MAX_TOKENS,
                    stream=False
                )

                raw_reply = completion.choices[0].message.content
                reply = self.clean_response(raw_reply)

                if reply:
                    self.memory.add_message(chat_id=chat_id, role="assistant", content=reply)
                    return reply

            except RateLimitError:
                logger.warning(f"Rate limit on {model}, trying fallback...")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error on {model}: {e}")
                continue

        return None

    # --- دستورات عمومی ---
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == ChatType.PRIVATE and update.effective_user.id != Config.OWNER_ID:
            await update.message.reply_text("این یک ربات شخصی است و دسترسی عمومی در پیوی ندارد.")
            return
        await update.message.reply_text("سلام! من روکسی هستم.")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == ChatType.PRIVATE and update.effective_user.id != Config.OWNER_ID:
            return
        help_text = (
            "📚 *دستورات ادمین گروه:*\n"
            "/ban - بن کاربر\n"
            "/kick - اخراج کاربر\n"
            "/mute - بی‌صدا کردن\n"
            "/unmute - رفع بی‌صدا\n"
            "/warn - اخطار\n"
            "/clearhistory - پاک کردن حافظه"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def clear_history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.memory.clear_history(update.effective_chat.id)
        await update.message.reply_text("✅ حافظه پاک شد!")

    # --- دستورات مدیریت ---
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message:
            await update.message.reply_text("روی پیام کاربر ریپلای کن.")
            return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await update.message.reply_text(f"❌ کاربر {target.first_name} بن شد.")
        except Exception:
            await update.message.reply_text("خطا در انجام بن. دسترسی ادمین بررسی شود.")

    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
            await update.message.reply_text(f"👢 کاربر {target.first_name} اخراج شد.")
        except Exception: pass

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False))
            await update.message.reply_text(f"🔇 کاربر {target.first_name} بی‌صدا شد.")
        except Exception: pass

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True)
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=perms)
            await update.message.reply_text(f"✅ کاربر {target.first_name} رفع بی‌صدا شد.")
        except Exception: pass

    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        target = update.message.reply_to_message.from_user
        reason = " ".join(context.args) if context.args else "رعایت نکردن قوانین"
        await update.message.reply_text(f"⚠️ اخطار به {target.first_name}: {reason}")

    # --- دستورات مخصوص سازنده (993028263) ---
    async def mygroups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        if not self.active_groups:
            await update.message.reply_text("گروهی ثبت نشده است.")
            return
        msg = "📋 **لیست گروه‌ها:**\n\n"
        for gid, gtitle in self.active_groups.items():
            msg += f"🔹 {gtitle} | ID: `{gid}`\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        if not context.args:
            await update.message.reply_text("آیدی گروه را وارد کنید.")
            return
        try:
            target_chat_id = int(context.args[0])
            await context.bot.leave_chat(target_chat_id)
            if target_chat_id in self.active_groups:
                del self.active_groups[target_chat_id]
            await update.message.reply_text(f"✅ از گروه {target_chat_id} خارج شدم.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {e}")

    # --- پردازش اصلی پیام‌ها ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_name = update.effective_user.first_name or "کاربر"

        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            self.active_groups[chat_id] = update.effective_chat.title or f"گروه {chat_id}"

        # قفل پیوی برای غیر از سازنده
        if chat_type == ChatType.PRIVATE and user_id != Config.OWNER_ID:
            await update.message.reply_text("این یک ربات شخصی است و دسترسی عمومی در پیوی ندارد.")
            return

        user_text = ""
        if update.message.text:
            user_text = update.message.text.strip()
        elif update.message.caption:
            user_text = update.message.caption.strip()

        if update.message.photo:
            user_text = f"[فرستادن عکس] {user_text}".strip()
        elif update.message.animation:
            user_text = f"[فرستادن گیف] {user_text}".strip()
        elif update.message.sticker:
            user_text = f"[فرستادن استیکر] {user_text}".strip()

        if not user_text: return

        # بن هوشمند بدون دستور
        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP] and update.message.reply_to_message:
            ban_phrases = ["بنش کن", "بن کن", "اخراجش کن", "دیلیتش کن", "بکنش بیرون"]
            if any(phrase in user_text.lower() for phrase in ban_phrases):
                if await self.is_admin(update.effective_chat, user_id):
                    try:
                        target = update.message.reply_to_message.from_user
                        await context.bot.ban_chat_member(chat_id, target.id)
                        await update.message.reply_text(f"❌ کاربر {target.first_name} بن شد.")
                        return
                    except Exception: pass

        # شرط چت در گروه: فقط منشن، ریپلای یا اسم "روکسی"
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        mentions_bot = any(kw in user_text.lower() for kw in ["روکسی", "roxie", f"@{bot_username}"])

        is_group = chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        # بررسی کول‌داون ۶ ثانیه
        if self.is_cooling_down(chat_id):
            return

        self.update_cooldown(chat_id)

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        response = await self.generate_response(
            chat_id=chat_id, user_id=user_id, user_name=user_name, text=user_text
        )

        if response:
            if is_reply_to_bot or mentions_bot or chat_type == ChatType.PRIVATE:
                await update.message.reply_text(response)
            else:
                await context.bot.send_message(chat_id=chat_id, text=response)

def main():
    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("clearhistory", bot.clear_history_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("kick", bot.kick_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))
    app.add_handler(CommandHandler("warn", bot.warn_command))

    app.add_handler(CommandHandler("mygroups", bot.mygroups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))

    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), bot.handle_message))

    print("🤖 روکسی با فارسی روان و بدون پیشوند راه‌اندازی شد...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
