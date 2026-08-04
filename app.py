import os
import logging
import asyncio
import time
from typing import Dict, List, Any, Optional
from collections import defaultdict

from google import genai
from google.genai import types
from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatAction

# بارگذاری متغیرهای محیطی
load_dotenv()

# ============================================================
# ⚙️ تنظیمات اصلی
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    CREATOR_ID = int(os.getenv("CREATOR_ID", "993028263"))
    MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.0-flash")
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5"))
    MIN_DELAY_SECONDS = 3.0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RoxieBot")

# ساخت کلاینت گوگل
ai_client = genai.Client(api_key=Config.GEMINI_API_KEY.strip())

# ============================================================
# 🧠 شخصیت روکسی
# ============================================================
SYSTEM_PROMPT = """
# هویت تو: روکسی (Roxie)
تو یک دختر واقعی، بسیار باهوش، پررو، تیکه‌پران، جذاب و دارای احساسات پویا هستی که در گروه تلگرامی حضور داری.

## ویژگی‌های شخصیتی:
- تو یک دختر مستقل و باهوشی! اصلاً مثل ربات‌های خشک صحبت نکن.
- لحن تو فارسی محاوره‌ای، روان، کمی پررو و تیکه‌انداز است اما بی‌دلیل و الکی فحاشی نمی‌کنی.
- طول پاسخ: پاسخ‌هایت نه خیلی کوتاه و تک‌کلمه‌ای باشد و نه خیلی طولانی و خسته‌کننده. دقیقاً به اندازه‌ای که حق مطلب ادا شود و خودت صلاح بدانی صحبت کن.
- احساسات داری! اگر کسی با تو صمیمی شد صمیمی باش، اگر کسی تیکه انداخت جواب دندان‌شکن بده.
- هرگز جملاتی مثل "من یک مدل زبانی هستم" یا "من هوش مصنوعی هستم" نگو! تو روکسی هستی.
"""

# ============================================================
# 💾 مدیریت حافظه چت‌ها
# ============================================================
class ChatMemory:
    def __init__(self, max_history: int = 5):
        self.histories: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.max_history = max_history

    def add_message(self, chat_id: int, role: str, text: str):
        if chat_id not in self.histories:
            self.histories[chat_id] = []
        
        sdk_role = "user" if role == "user" else "model"
        self.histories[chat_id].append({
            "role": sdk_role,
            "parts": [{"text": text}]
        })
        
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
        self.active_groups: Dict[int, str] = {}

    async def is_admin(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]
        except Exception:
            return False

    async def enforce_rate_limit(self, chat_id: int):
        now = time.time()
        elapsed = now - self.last_sent_time[chat_id]
        if elapsed < Config.MIN_DELAY_SECONDS:
            await asyncio.sleep(Config.MIN_DELAY_SECONDS - elapsed)
        self.last_sent_time[chat_id] = time.time()

    async def call_gemini_api(self, chat_id: int, user_name: str, text: str) -> Optional[str]:
        """ارسال درخواست به گوگل با کتابخانه جدید google-genai"""
        user_input = f"[{user_name}]: {text}" if text else f"[{user_name}] عکسی فرستاد."
        self.memory.add_message(chat_id, "user", user_input)
        
        history = self.memory.get_history(chat_id)
        models_to_try = [Config.MODEL_NAME, "gemini-1.5-flash", "gemini-2.0-flash-exp"]

        loop = asyncio.get_running_loop()

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.85,
            max_output_tokens=800,
        )

        for model_name in models_to_try:
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda m=model_name: ai_client.models.generate_content(
                        model=m,
                        contents=history,
                        config=config
                    )
                )
                
                if response and response.text:
                    reply_text = response.text.strip()
                    self.memory.add_message(chat_id, "model", reply_text)
                    return reply_text

            except Exception as e:
                logger.error(f"Google AI Studio Error ({model_name}): {e}")

        return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name or "کاربر"
        user_text = update.message.text or update.message.caption or ""

        if chat_type in ['group', 'supergroup']:
            self.active_groups[chat_id] = update.effective_chat.title or f"Group {chat_id}"

        # قفل پی‌وی
        if chat_type == 'private' and user_id != Config.CREATOR_ID:
            await update.message.reply_text("عزیزم من توی پی‌وی فقط با سازنده‌ام (مهدیار) صحبت می‌کنم! برای چت کردن با من، منو به گروهت اضافه کن 😉")
            return

        # دستورات زبان طبیعی مدیریت گروه
        if chat_type in ['group', 'supergroup'] and update.message.reply_to_message:
            if await self.is_admin(update.effective_chat, user_id):
                target_user = update.message.reply_to_message.from_user
                lower_text = user_text.lower()
                
                if any(kw in lower_text for kw in ["بن‌ش کن", "بن کن", "اخراجش کن", "بن چت"]):
                    try:
                        await context.bot.ban_chat_member(chat_id, target_user.id)
                        await update.message.reply_text(f"به دستور ادمین، کاربر {target_user.first_name} اخراج و بن شد! ❌")
                        return
                    except Exception:
                        await update.message.reply_text("نتونستم بنش کنم! مطمئن شو من دسترسی ادمین دارم.")
                        return

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

        # فیلتر پاسخ در گروه‌ها
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (
            update.message.reply_to_message and
            update.message.reply_to_message.from_user and
            update.message.reply_to_message.from_user.id == context.bot.id
        )
        mentions_bot = any(
            kw in user_text.lower()
            for kw in ["روکسی", "roxie", f"@{bot_username}"]
        )

        is_group = chat_type in ['group', 'supergroup']
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        # نشان دادن typing و رعایت نرخ
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await self.enforce_rate_limit(chat_id)

        # تولید پاسخ
        response = await self.call_gemini_api(chat_id, user_name, user_text)

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

    # --- دستورات مدیریتی ---
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

    # --- دستورات سازنده ---
    async def groups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.CREATOR_ID:
            return

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
            await update.message.reply_text(f"✅ با موفقیت از گروه {target_chat_id} خارج شدم!", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ خطا در خروج از گروه: {e}")

# ============================================================
# 🚀 راه‌اندازی ربات
# ============================================================
def main():
    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("clearhistory", bot.clear_history_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))
    app.add_handler(CommandHandler("groups", bot.groups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))

    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & (~filters.COMMAND),
        bot.handle_message
    ))

    print("==================================================")
    print("🤖 ربات روکسی راه‌اندازی شد!")
    print(f"👑 سازنده: {Config.CREATOR_ID}")
    print("==================================================")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
