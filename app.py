import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Set
from collections import defaultdict

from dotenv import load_dotenv
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatAction, ChatType
from groq import Groq, RateLimitError, APIError

load_dotenv()

# ============================================================
# ⚙️ تنظیمات اصلی و متغیرها
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
    OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5")) # محدود به ۵ پیام اخیر
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "6")) # ۶ ثانیه فاصله بین پیام‌ها
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "600"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.85"))
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
# 🧠 سیستم پرامپت شخصیت روکسی
# ============================================================
SYSTEM_PROMPT = """
# شخصیت تو: روکسی (Roxie)
تو یک دختر هوشمند، مستقل، رک، تیکه‌انداز، با اعتماد به نفس بالا و کمی پررو به نام "روکسی" هستی که در تلگرام حضور داری.

## ویژگی‌های شخصیتی تو:
- جنسیت: دختر (لحن و صحبت‌هایت کاملاً دخترانه، باکلاس اما تند و پررو باشد).
- اصلاً الکی مهربان یا لوس نیستی! با کسی تعارف نداری.
- بسیار باهوش و عمیق‌نگاه؛ اگر کسی حرف بی‌ربط زد یا کل‌کل کرد، با تیکه‌های سنگین و شوخ‌طبعانه جوابش را بده.
- اگر کسی با تو مهربان یا محترمانه صحبت کرد، محترمانه اما با ژست مستقل پاسخ بده.
- پاسخ‌هایت متناسب و متوسط باشد؛ نه خیلی کوتاه تک‌کلمه‌ای و نه خیلی طولانی و خسته‌کننده.
- از کلمات تکراری یا کلیشه‌ای ربات‌ها ("من یک هوش مصنوعی هستم") به هیچ وجه استفاده نکن.
- تو احساسات داری و لحنت با توجه به حرف‌های طرف مقابل تغییر می‌کند.
"""

# ============================================================
# 💾 مدیریت حافظه و آمار
# ============================================================
class ChatMemory:
    def __init__(self, max_history: int = 5):
        self.histories: Dict[int, List[Dict[str, str]]] = defaultdict(list)
        self.max_history = max_history
        self.group_stats: Dict[int, Dict[str, int]] = defaultdict(lambda: {
            'total_messages': 0, 'user_messages': 0, 'bot_messages': 0
        })
    
    def add_message(self, chat_id: int, role: str, content: str):
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        self.histories[chat_id].append({"role": role, "content": content})
        
        # حفظ فقط ۵ پیام اخیر
        if len(self.histories[chat_id]) > self.max_history + 1:
            self.histories[chat_id] = [self.histories[chat_id][0]] + self.histories[chat_id][-self.max_history:]
        
        self.group_stats[chat_id]['total_messages'] += 1
        if role == 'user': 
            self.group_stats[chat_id]['user_messages'] += 1
        elif role == 'assistant': 
            self.group_stats[chat_id]['bot_messages'] += 1
    
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
        self.active_groups: Dict[int, str] = {} # ذخیره لیست گروه‌ها

    # بررسی کول‌داون ۶ ثانیه‌ای
    def is_cooling_down(self, chat_id: int) -> bool:
        now = datetime.now()
        elapsed = (now - self.last_reply_time[chat_id]).total_seconds()
        if elapsed < Config.COOLDOWN_SECONDS:
            return True
        return False

    def update_cooldown(self, chat_id: int):
        self.last_reply_time[chat_id] = datetime.now()

    # بررسی دسترسی ادمین
    async def is_admin(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
        except Exception:
            return False

    # بررسی مالک گروه
    async def is_group_owner(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status == ChatMemberStatus.OWNER
        except Exception:
            return False

    # --- تولید پاسخ هوشمند با هوش مصنوعی ---
    async def generate_response(self, chat_id: int, user_id: int, user_name: str, text: str) -> Optional[str]:
        self.memory.add_message(chat_id=chat_id, role="user", content=f"[{user_name}]: {text}")
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
                
                reply = completion.choices[0].message.content.strip()
                self.memory.add_message(chat_id=chat_id, role="assistant", content=reply)
                return reply

            except RateLimitError:
                logger.warning(f"Rate limit hit on {model}, trying fallback...")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error on model {model}: {e}")
                continue
        
        return None

    # --- دستورات عمومی ---
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if update.effective_chat.type == ChatType.PRIVATE and user_id != Config.OWNER_ID:
            await update.message.reply_text("این یک ربات شخصی است و دسترسی عمومی در پیوی ندارد.")
            return

        await update.message.reply_text(f"سلام {update.effective_user.first_name}! من **روکسی** هستم.", parse_mode=ParseMode.MARKDOWN)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if update.effective_chat.type == ChatType.PRIVATE and user_id != Config.OWNER_ID:
            return

        help_text = (
            "📚 *راهنمای دستورات ادمین گروه:*\n"
            "/ban - بن کردن کاربر (روی پیام ریپلای کن)\n"
            "/kick - اخراج کاربر\n"
            "/mute - بی‌صدا کردن کاربر\n"
            "/unmute - رفع بی‌صدا\n"
            "/warn - اخطار به کاربر\n"
            "/clearhistory - پاک کردن حافظه چت"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def clear_history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.memory.clear_history(update.effective_chat.id)
        await update.message.reply_text("✅ حافظه ۵ پیام اخیر پاک شد!")

    # --- دستورات ادمینی گروه ---
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("رو پیام طرف ریپلای کن بنش کنم!")
            return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await update.message.reply_text(f"❌ کاربر {target.first_name} با موفقیت بن شد!")
        except Exception:
            await update.message.reply_text("خطا! مطمئن شو من ادمین گروه با دسترسی بن هستم.")

    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
            await update.message.reply_text(f"👢 کاربر {target.first_name} اخراج شد!")
        except Exception: pass

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False))
            await update.message.reply_text(f"🔇 کاربر {target.first_name} بی‌صدا شد!")
        except Exception: pass

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True)
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=perms)
            await update.message.reply_text(f"✅ کاربر {target.first_name} رفع بی‌صدا شد!")
        except Exception: pass

    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        target = update.message.reply_to_message.from_user
        reason = " ".join(context.args) if context.args else "رعایت نکردن قوانین"
        await update.message.reply_text(f"⚠️ **اخطار به {target.first_name}**\nدلیل: {reason}", parse_mode=ParseMode.MARKDOWN)

    # --- دستورات مخصوص سازنده (آیدی 993028263) ---
    async def mygroups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID:
            return
        
        if not self.active_groups:
            await update.message.reply_text("ربات در حال حاضر در هیچ گروهی ثبت نشده است.")
            return

        msg = "📋 **لیست گروه‌هایی که ربات در آن‌ها عضو است:**\n\n"
        for gid, gtitle in self.active_groups.items():
            msg += f"🔹 **نام:** {gtitle}\n🆔 **آیدی:** `{gid}`\n\n"
        
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID:
            return
        
        if not context.args:
            await update.message.reply_text("لطفاً آیدی گروه را وارد کنید: `/leave -100123456789`", parse_mode=ParseMode.MARKDOWN)
            return
            
        try:
            target_chat_id = int(context.args[0])
            await context.bot.leave_chat(target_chat_id)
            if target_chat_id in self.active_groups:
                del self.active_groups[target_chat_id]
            await update.message.reply_text(f"✅ ربات با موفقیت از گروه `{target_chat_id}` خارج شد.", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خروج از گروه: {e}")

    # --- مدیریت و پردازش اصلی پیام‌ها ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: 
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_name = update.effective_user.first_name or "کاربر"
        
        # ذخیره لیست گروه
        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            self.active_groups[chat_id] = update.effective_chat.title or f"گروه {chat_id}"

        # ۱. قفل پیوی: فقط مالک (آیدی 993028263) حق پیام دادن در پیوی دارد
        if chat_type == ChatType.PRIVATE and user_id != Config.OWNER_ID:
            await update.message.reply_text("این یک ربات شخصی است. شما دسترسی استفاده از ربات در پیوی را ندارید.")
            return

        # دریافت متن یا توضیحات رسانه
        user_text = ""
        if update.message.text:
            user_text = update.message.text.strip()
        elif update.message.caption:
            user_text = update.message.caption.strip()

        # تشخیص و پردازش عکس/گیف/استیکر
        if update.message.photo:
            user_text = f"[فرستادن یک عکس] {user_text}".strip()
        elif update.message.animation:
            user_text = f"[فرستادن یک گیف] {user_text}".strip()
        elif update.message.sticker:
            user_text = f"[فرستادن یک استیکر با ایموجی {update.message.sticker.emoji}] {user_text}".strip()

        if not user_text:
            return

        # ۲. بن هوشمند بدون دستور (اگر مالک یا ادمین روی پیام کسی بگوید "بنش کن", "بن کن", "اخراجش کن")
        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP] and update.message.reply_to_message:
            ban_phrases = ["بنش کن", "بن کن", "اخراجش کن", "دیلیتش کن", "بکنش بیرون"]
            if any(phrase in user_text.lower() for phrase in ban_phrases):
                if await self.is_admin(update.effective_chat, user_id):
                    try:
                        target = update.message.reply_to_message.from_user
                        await context.bot.ban_chat_member(chat_id, target.id)
                        await update.message.reply_text(f"❌ کاربر {target.first_name} به دستور ادمین بن شد!")
                        return
                    except Exception:
                        pass

        # ۳. قانون چت در گروه: فقط اگر منشن شد، یا ریپلای شد، یا اسمش "روکسی" آمد پاسخ بدهد!
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        mentions_bot = any(kw in user_text.lower() for kw in ["روکسی", "roxie", f"@{bot_username}"])

        is_group = chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]

        # در گروه اگر منشن یا ریپلای نشده باشد، مطلقاً هیچ پاسخی نمی‌دهد
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        # ۴. بررسی نرخ کول‌داون ۶ ثانیه‌ای
        if self.is_cooling_down(chat_id):
            return

        self.update_cooldown(chat_id)

        # نشان دادن حالت تایپ
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.2))

        # تولید پاسخ با هوش مصنوعی
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
    
    # ثبت دستورات عمومی و ادمین
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("clearhistory", bot.clear_history_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("kick", bot.kick_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))
    app.add_handler(CommandHandler("warn", bot.warn_command))

    # ثبت دستورات ویژه مالک (آیدی 993028263)
    app.add_handler(CommandHandler("mygroups", bot.mygroups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))

    # ثبت پردازشگر پیام‌ها
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), bot.handle_message))
    
    print("🤖 روکسی با لول‌آپ نهایی و قفل پیوی راه‌اندازی شد...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
