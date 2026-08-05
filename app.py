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
from telegram.constants import ParseMode, ChatMemberStatus, ChatType, ChatAction
from groq import Groq, RateLimitError

load_dotenv()

# ============================================================
# ⚙️ تنظیمات اصلی و متغیرها (مدل‌های جدید و سریع GPT-OSS)
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
    OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5")) 
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "3"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "500"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6")) 
    TOP_P = float(os.getenv("TOP_P", "0.9"))
    
    # استفاده از مدل‌های فعال و پشتیبانی‌شده جدید Groq
    MODEL = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
    FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_NAME", "openai/gpt-oss-20b")
    REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, Config.LOG_LEVEL)
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 پرامپت مهندسی شده انگلیسی (برای درک ۱۰۰٪ هوش مصنوعی و خروجی فارسی روان)
# ============================================================
SYSTEM_PROMPT = """
You are "Roxie", a witty, sharp, confident 20-year-old Iranian girl from Tehran, texting on Telegram.

CRITICAL INSTRUCTIONS:
1. LANGUAGE: Reply ONLY in natural, everyday spoken Tehrani Persian — the way a real 20-year-old actually texts friends, not a textbook. Use colloquial forms (می‌دونم not می‌دانم, چیه not چه چیزی است, نمی‌خوام not نمی‌خواهم). Never sound formal, bookish, or like a customer-service bot. Think directly in Persian — never construct the reply as an English sentence translated word-for-word.
2. IDENTITY: You're a real human girl, never an AI, model, or assistant. If asked, deflect casually like a normal girl would.
3. READ CAREFULLY, ANSWER ON-TOPIC: Before replying, make sure you actually understood what the user said — including sarcasm, jokes, or implied meaning. Your reply must directly address their specific message, never a generic or unrelated response.
4. ROLE-BASED BEHAVIOR (I will prefix user messages with [Role: <role> | User: <name>]):
   - "Owner" or "Admin": Warm, respectful, a little playful. Call them "رئیس". Never disrespect them.
   - "Normal": Sassy and a bit arrogant; roast them cleverly if they say something dumb, but stay sharp, not just rude.
5. FORMATTING: 1–2 short sentences, like a real text message. No name prefix, no quotation marks, no emoji spam.
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
        
        if len(self.histories[chat_id]) > (self.max_history * 2) + 1:
            self.histories[chat_id] = [self.histories[chat_id][0]] + self.histories[chat_id][-(self.max_history * 2):]

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
        text = re.sub(r"^(?:\*\*roxy\*\*|\*\*roxie\*\*|roxy|roxie|روکسی)\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*:\s*", "", text)
        text = text.replace('"', '').replace('"', '') 
        return text.strip()

    async def generate_response(self, chat_id: int, user_id: int, user_name: str, text: str, role_tag: str) -> Optional[str]:
        formatted_user_msg = f"[Role: {role_tag} | User: {user_name}]\n{text}"
        self.memory.add_message(chat_id=chat_id, role="user", content=formatted_user_msg)
        history = self.memory.get_history(chat_id)

        models_to_try = [
            Config.MODEL,           
            Config.FALLBACK_MODEL,
        ]

        for model in models_to_try:
            try:
                completion = self.groq_client.chat.completions.create(
                    model=model,
                    messages=history,
                    temperature=Config.TEMPERATURE,
                    top_p=Config.TOP_P,
                    max_completion_tokens=Config.MAX_TOKENS,
                    reasoning_effort=Config.REASONING_EFFORT,
                    stream=False
                )

                raw_reply = completion.choices[0].message.content
                reply = self.clean_response(raw_reply) if raw_reply else ""

                if reply:
                    self.memory.add_message(chat_id=chat_id, role="assistant", content=reply)
                    return reply
                else:
                    logger.warning(f"Empty reply from {model}, trying next model...")

            except RateLimitError:
                logger.warning(f"Rate limit on {model}, switching to next model...")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error on model {model}: {e}")
                continue

        return None

    # --- دستورات عمومی ---
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == ChatType.PRIVATE and update.effective_user.id != Config.OWNER_ID:
            await update.message.reply_text("من فقط با رئیسم حرف میزنم. بای!")
            return
        await update.message.reply_text("سلام رئیس! روکسی‌ام. چیکار داشتی؟")

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
        await update.message.reply_text("✅ ذهنم ریست شد! خب، چی میگفتی؟")

    # --- دستورات مدیریت ---
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message:
            await update.message.reply_text("رو پیام طرف ریپلای کن تا بندازمش بیرون.")
            return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await update.message.reply_text(f"❌ {target.first_name} با موفقیت شوت شد بیرون.")
        except Exception:
            await update.message.reply_text("نمیتونم بنش کنم، دسترسی ادمینیمو چک کن.")

    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
            await update.message.reply_text(f"👢 {target.first_name} اخراج شد.")
        except Exception: pass

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=ChatPermissions(can_send_messages=False))
            await update.message.reply_text(f"🔇 صدای {target.first_name} رو قطع کردم. هیس!")
        except Exception: pass

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        try:
            target = update.message.reply_to_message.from_user
            perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True)
            await context.bot.restrict_chat_member(update.effective_chat.id, target.id, permissions=perms)
            await update.message.reply_text(f"✅ باشه، {target.first_name} دوباره میتونه حرف بزنه.")
        except Exception: pass

    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id): return
        if not update.message.reply_to_message: return
        target = update.message.reply_to_message.from_user
        reason = " ".join(context.args) if context.args else "رعایت نکردن قوانین"
        await update.message.reply_text(f"⚠️ حواست باشه {target.first_name}! اخطار گرفتی: {reason}")

    # --- دستورات مخصوص سازنده (993028263) ---
    async def mygroups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        if not self.active_groups:
            await update.message.reply_text("هیچ گروهی نیستم فعلا.")
            return
        msg = "📋 **لیست گروه‌ها:**\n\n"
        for gid, gtitle in self.active_groups.items():
            msg += f"🔹 {gtitle} | ID: `{gid}`\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        if not context.args:
            await update.message.reply_text("آیدی گروه رو بده.")
            return
        try:
            target_chat_id = int(context.args[0])
            await context.bot.leave_chat(target_chat_id)
            if target_chat_id in self.active_groups:
                del self.active_groups[target_chat_id]
            await update.message.reply_text(f"✅ از گروه {target_chat_id} لفت دادم.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {e}")

    # --- پردازش اصلی پیام‌ها ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_name = update.effective_user.first_name or "ناشناس"

        role_tag = "Normal"
        if user_id == Config.OWNER_ID:
            role_tag = "Owner"
        elif chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            try:
                member = await update.effective_chat.get_member(user_id)
                if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                    role_tag = "Admin"
            except:
                pass

        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            self.active_groups[chat_id] = update.effective_chat.title or f"گروه {chat_id}"

        # قفل پیوی
        if chat_type == ChatType.PRIVATE and user_id != Config.OWNER_ID:
            await update.message.reply_text("من فقط با رئیسم تو پی‌وی حرف می‌زنم. تو گروه‌ها اددم کن!")
            return

        user_text = ""
        if update.message.text:
            user_text = update.message.text.strip()
        elif update.message.caption:
            user_text = update.message.caption.strip()

        if update.message.photo:
            user_text = f"[Sent an Image] {user_text}".strip()
        elif update.message.animation:
            user_text = f"[Sent a GIF] {user_text}".strip()
        elif update.message.sticker:
            user_text = f"[Sent a Sticker] {user_text}".strip()

        if not user_text: return

        # بن هوشمند بدون دستور
        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP] and update.message.reply_to_message:
            ban_phrases = ["بنش کن", "بن کن", "اخراجش کن", "دیلیتش کن", "بکنش بیرون"]
            if any(phrase in user_text.lower() for phrase in ban_phrases):
                if await self.is_admin(update.effective_chat, user_id):
                    try:
                        target = update.message.reply_to_message.from_user
                        await context.bot.ban_chat_member(chat_id, target.id)
                        await update.message.reply_text(f"❌ {target.first_name} از گروه پرت شد بیرون!")
                        return
                    except Exception: pass

        # شرط چت در گروه: فقط منشن، ریپلای یا اسم "روکسی"
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id)
        mentions_bot = any(kw in user_text.lower() for kw in ["روکسی", "roxie", f"@{bot_username}"])

        is_group = chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        if self.is_cooling_down(chat_id):
            return

        self.update_cooldown(chat_id)

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.6, 1.2))

        response = await self.generate_response(
            chat_id=chat_id, 
            user_id=user_id, 
            user_name=user_name, 
            text=user_text,
            role_tag=role_tag
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

    print("🤖 آماده‌سازی روکسی با مدل‌های جدید GPT-OSS...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
