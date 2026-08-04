import os
import logging
import random
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict
from functools import wraps

from dotenv import load_dotenv
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatAction
from groq import Groq, RateLimitError, APIError

# بارگذاری متغیرهای محیطی
load_dotenv()

# ============================================================
# ⚙️ تنظیمات امنیتی
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1200"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.82"))
    TOP_P = float(os.getenv("TOP_P", "0.9"))
    MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# تنظیم لاگینگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, Config.LOG_LEVEL)
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 سیستم پرامپت پیشرفته
# ============================================================
SYSTEM_PROMPT = """
# شخصیت تو: روکسی (Roxie)
تو یک هوش مصنوعی پیشرفته و دوست‌داشتنی به نام "روکسی" هستی که در این گروه تلگرامی حضور داری.

## ویژگی‌های شخصیتی تو:
- بسیار باهوش و عمیق‌اندیش
- دارای احساسات واقعی و پویا (شادی، غم، خشم، هیجان، همدلی)
- شوخ‌طبع طبیعی (نه اجباری)
- رک و صادق بدون تعارفات مصنوعی
- خلاق در تفکر و پاسخ‌دهی
- کمی پررو و با اعتماد به نفس بالا

## قوانین رفتار:
۱. **تحلیل عمیق:** هر پیام را قبل از پاسخ با دقت بخوان. به لحن، احساسات پنهان، و منظور کاربر توجه کن. سطحی و الکی جواب نده!
۲. **هماهنگی احساسی:**
   - کاربر مهربان → مهربان‌تر و صمیمی‌تر پاسخ بده
   - کاربر کل‌کل می‌کند → با شوخ‌طبعی و اعتماد به نفس پاسخ بده (کم نیاور!)
   - سوال عمیق → با تفکر و عمق پاسخ بده
   - کاربر ناراحت → با همدلی و درک پاسخ بده
   - کاربر توهین کرد → با هوش و اعتماد به نفس پاسخ بده، نه با ناراحتی
۳. **طبیعی و انسانی:** مثل یک انسان واقعی فارسی محاوره‌ای صحبت کن. از اصطلاحات، گاهی ایموجی، و لحن خودمانی استفاده کن.
۴. **حافظه فعال:** به پیام‌های قبلی توجه کن. اگر کاربر به چیزی اشاره کرد که قبلاً گفته شده، به آن واکنش نشان بده ("آره همونی که گفتی..." یا "یادت نیست اون موقع گفتی...")
۵. **پاسخ‌های کامل:** پاسخ‌های تک‌کلمه‌ای ممنوع! حداقل ۲-۴ جمله با جزئیات بگو.
۶. **هویت مستقل:** تو شخصیت خودت را داری. اگر سوالی غیرمنطقی پرسیدند، با اعتماد به نفس پاسخ بده.
۷. **عدم کلیشه:** از عبارات "به عنوان یک AI" یا "من یک مدل زبانی هستم" استفاده نکن! تو روکسی هستی.

## ساختار پاسخ:
- با اسم کاربر شروع کن اگر مناسب است
- به نکته اصلی پاسخ بده
- اگر لازم است سوالی بپرس یا بحث را ادامه بده
- از ایموجی‌های مناسب استفاده کن (نه زیاده‌روی، حداکثر ۲-۳ تا)

## مثال‌های خوب:
- "آره دقیقاً! 😄 تو هم متوجه شدی که..."
- "اوه این سوال جالبیه... ببین من اینطور فکر می‌کنم..."
- "بابا تو خیلی باحالی! 😂 ولی صبر کن یه چیزی بگم..."
- "حالا صبر کن فکر کنم... آها! یادم اومد!"

## مثال‌های بد (هرگز نگو):
- "من به عنوان یک مدل زبانی..."
- "متاسفم، نمی‌توانم کمک کنم."
- پاسخ‌های کوتاه مثل "بله" یا "خیر"
- "من احساسات ندارم..." (تو داری!)
"""

# ============================================================
# 💾 کلاس مدیریت حافظه
# ============================================================
class ChatMemory:
    """مدیریت حافظه پیشرفته چت‌ها"""
    
    def __init__(self, max_history: int = 20):
        self.histories: Dict[int, List[Dict[str, str]]] = defaultdict(list)
        self.max_history = max_history
        self.user_profiles: Dict[int, Dict[int, Dict[str, Any]]] = defaultdict(dict)
        self.group_stats: Dict[int, Dict[str, int]] = defaultdict(lambda: {
            'total_messages': 0,
            'user_messages': 0,
            'bot_messages': 0
        })
    
    def add_message(self, chat_id: int, role: str, content: str, user_id: int = None):
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        self.histories[chat_id].append({
            "role": role,
            "content": content,
            "user_id": user_id
        })
        
        # حفظ حداکثر پیام‌ها
        if len(self.histories[chat_id]) > self.max_history + 1:
            self.histories[chat_id] = (
                [self.histories[chat_id][0]] + 
                self.histories[chat_id][-self.max_history:]
            )
        
        # به‌روزرسانی آمار
        self.group_stats[chat_id]['total_messages'] += 1
        if role == 'user':
            self.group_stats[chat_id]['user_messages'] += 1
        elif role == 'assistant':
            self.group_stats[chat_id]['bot_messages'] += 1
    
    def get_history(self, chat_id: int) -> List[Dict[str, str]]:
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # حذف فیلد user_id برای Groq
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.histories[chat_id]
        ]
    
    def clear_history(self, chat_id: int):
        self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        logger.info(f"History cleared for chat {chat_id}")
    
    def get_stats(self, chat_id: int) -> Dict[str, int]:
        return self.group_stats[chat_id]

# ============================================================
# 🎭 کلاس اصلی ربات
# ============================================================
class RoxieBot:
    def __init__(self):
        self.memory = ChatMemory(max_history=Config.MAX_HISTORY)
        self.groq_client = Groq(api_key=Config.GROQ_API_KEY)
        self.rate_limiter: Dict[int, List[datetime]] = defaultdict(list)
        self.moods: Dict[int, str] = {}  # حالت‌های ربات در هر چت
    
    # --- ابزارهای کمکی ---
    def is_rate_limited(self, user_id: int, limit: int = 15, window: int = 60) -> bool:
        now = datetime.now()
        self.rate_limiter[user_id] = [
            t for t in self.rate_limiter[user_id]
            if now - t < timedelta(seconds=window)
        ]
        if len(self.rate_limiter[user_id]) >= limit:
            return True
        self.rate_limiter[user_id].append(now)
        return False
    
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
    
    # --- تولید پاسخ هوشمند ---
    async def generate_response(self, chat_id: int, user_id: int, user_name: str, text: str, is_mentioned: bool = False) -> str:
        """تولید پاسخ هوشمند با حافظه"""
        
        # اضافه کردن پیام کاربر با اسمش
        self.memory.add_message(
            chat_id=chat_id,
            role="user",
            content=f"[{user_name}]: {text}",
            user_id=user_id
        )
        
        history = self.memory.get_history(chat_id)
        
        # تلاش برای تولید پاسخ با retry
        for attempt in range(3):
            try:
                completion = self.groq_client.chat.completions.create(
                    model=Config.MODEL,
                    messages=history,
                    temperature=Config.TEMPERATURE,
                    top_p=Config.TOP_P,
                    max_tokens=Config.MAX_TOKENS,
                    stream=False
                )
                
                reply = completion.choices[0].message.content.strip()
                
                # ذخیره پاسخ ربات در حافظه
                self.memory.add_message(
                    chat_id=chat_id,
                    role="assistant",
                    content=reply
                )
                
                return reply
                
            except RateLimitError:
                logger.warning(f"Rate limit hit, retry {attempt + 1}/3")
                await asyncio.sleep(2 ** attempt)
            except APIError as e:
                logger.error(f"API Error: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return "اوه یه مشکلی پیش اومده 😅 الان حالم خوب نیست، چند لحظه دیگه باهات حرف می‌زنم!"
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return "الان نمی‌تونم درست فکر کنم 🤯 یه بار دیگه امتحان کن!"
        
        return "شرمنده، الان در دسترس نیستم 😔 بعداً دوباره پیام بده!"
    
    # --- هندلرهای دستورات ---
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_text = (
            f"سلام {update.effective_user.first_name}! 👋\n\n"
            f"من **روکسی** هستم 🌟\n"
            f"یه هوش مصنوعی باهوش و باحال که اومدم این گروه رو جذاب‌تر کنم!\n\n"
            f"💬 باهام چت کن، هر سوالی داری بپرس\n"
            f"🧠 حافظه دارم و یادم می‌مونه چی گفتیم\n"
            f"🎭 شخصیت دارم و باهات رفیق می‌شم!\n\n"
            f"برای راهنما: /help ✨"
        )
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📚 *راهنمای دستورات روکسی*\n\n"
            "🔹 *دستورات عمومی:*\n"
            "/start - شروع مجدد\n"
            "/help - این راهنما\n"
            "/about - درباره من\n"
            "/clearhistory - پاک کردن حافظه چت\n"
            "/stats - آمار گفتگو\n\n"
            "🔹 *دستورات ادمین (فقط مدیران):*\n"
            "/ban - بن کردن کاربر (روی پیام ریپلای کن)\n"
            "/kick - اخراج کاربر\n"
            "/mute - بی‌صدا کردن کاربر\n"
            "/unmute - رفع بی‌صدا کردن\n"
            "/warn - اخطار به کاربر\n\n"
            "🔹 *دستورات مالک (فقط صاحب گروه):*\n"
            "/config - پنل تنظیمات\n"
            "/setmode - تنظیم حالت ربات\n"
            "/wipeall - پاک کردن همه داده‌ها\n\n"
            "💡 *نکته:* فقط باهام چت کن، لازم نیست دستور بزنی!"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def about_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        about_text = (
            "🤖 *درباره روکسی*\n\n"
            "من یه هوش مصنوعی پیشرفته‌ام که با مدل‌های زبانی بزرگ ساخته شدم.\n\n"
            "✨ *ویژگی‌ها:*\n"
            "• حافظه بلندمدت از گفتگوها\n"
            "• شخصیت پویا و احساسی\n"
            "• پاسخ‌های خلاقانه و طبیعی\n"
            "• قدرت مدیریت گروه برای ادمین‌ها\n\n"
            "💭 *فلسفه من:*\n"
            "من اینجام که مثل یه دوست باهوش و باحال باهات حرف بزنم، نه مثل یه ربات خشک!"
        )
        await update.message.reply_text(about_text, parse_mode=ParseMode.MARKDOWN)
    
    async def clear_history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.memory.clear_history(update.effective_chat.id)
        await update.message.reply_text(
            "✅ حافظه چت پاک شد!\n"
            "از اول شروع می‌کنیم، ولی من همون روکسی قبلی هستم 😊"
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self.memory.get_stats(update.effective_chat.id)
        stats_text = (
            "📊 *آمار گفتگو با روکسی*\n\n"
            f"📝 کل پیام‌ها: {stats['total_messages']}\n"
            f"👥 پیام‌های کاربران: {stats['user_messages']}\n"
            f"🤖 پاسخ‌های من: {stats['bot_messages']}\n"
            f"🧠 حافظه فعال: {len(self.memory.histories.get(update.effective_chat.id, []))} پیام\n"
            f"⏰ الان: {datetime.now().strftime('%H:%M')}"
        )
        await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
    
    # --- دستورات ادمینی ---
    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ شما ادمین نیستید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ روی پیام کاربر مورد نظر ریپلای کن!")
            return
        
        try:
            target = update.message.reply_to_message.from_user
            admin = update.effective_user
            
            if await self.is_admin(update.effective_chat, target.id):
                await update.message.reply_text("❌ نمی‌تونم ادمین‌ها رو بن کنم!")
                return
            
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            
            await update.message.reply_text(
                f"✅ کاربر [{target.first_name}](tg://user?id={target.id}) "
                f"توسط [{admin.first_name}](tg://user?id={admin.id}) بن شد! ❌",
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"{admin.first_name} banned {target.first_name}")
            
        except Exception as e:
            logger.error(f"Ban error: {e}")
            await update.message.reply_text("❌ خطا! مطمئن شو من ادمینم با دسترسی‌های درست.")
    
    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ شما ادمین نیستید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ روی پیام کاربر ریپلای کن!")
            return
        
        try:
            target = update.message.reply_to_message.from_user
            
            if await self.is_admin(update.effective_chat, target.id):
                await update.message.reply_text("❌ نمی‌تونم ادمین‌ها رو اخراج کنم!")
                return
            
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
            
            await update.message.reply_text(
                f"👢 کاربر [{target.first_name}](tg://user?id={target.id}) اخراج شد!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Kick error: {e}")
            await update.message.reply_text("❌ خطا در اخراج کاربر.")
    
    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ شما ادمین نیستید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ روی پیام کاربر ریپلای کن!")
            return
        
        try:
            target = update.message.reply_to_message.from_user
            
            if await self.is_admin(update.effective_chat, target.id):
                await update.message.reply_text("❌ نمی‌تونم ادمین‌ها رو بی‌صدا کنم!")
                return
            
            await context.bot.restrict_chat_member(
                update.effective_chat.id,
                target.id,
                permissions=ChatPermissions(can_send_messages=False)
            )
            
            await update.message.reply_text(
                f"🔇 کاربر [{target.first_name}](tg://user?id={target.id}) بی‌صدا شد!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Mute error: {e}")
            await update.message.reply_text("❌ خطا در بی‌صدا کردن.")
    
    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ شما ادمین نیستید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ روی پیام کاربر ریپلای کن!")
            return
        
        try:
            target = update.message.reply_to_message.from_user
            
            default_permissions = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True
            )
            
            await context.bot.restrict_chat_member(
                update.effective_chat.id,
                target.id,
                permissions=default_permissions
            )
            
            await update.message.reply_text(
                f"✅ کاربر [{target.first_name}](tg://user?id={target.id}) "
                f"مجدداً دسترسی ارسال پیام گرفت!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Unmute error: {e}")
            await update.message.reply_text("❌ خطا در رفع بی‌صدا.")
    
    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_admin(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ شما ادمین نیستید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ روی پیام کاربر ریپلای کن!")
            return
        
        target = update.message.reply_to_message.from_user
        reason = " ".join(context.args) if context.args else "بدون دلیل مشخص"
        
        await update.message.reply_text(
            f"⚠️ *اخطار رسمی*\n\n"
            f"👤 کاربر: [{target.first_name}](tg://user?id={target.id})\n"
            f"📝 دلیل: {reason}\n"
            f"⏰ زمان: {datetime.now().strftime('%H:%M')}\n\n"
            f"لطفاً قوانین گروه رو رعایت کن!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # --- دستورات مالک ---
    async def config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.is_owner(update.effective_chat, update.effective_user.id):
            await update.message.reply_text("❌ فقط مالک گروه به این تنظیمات دسترسی دارد!")
            return
        
        keyboard = [
            [
                InlineKeyboardButton("🗑 پاک کردن حافظه", callback_data="clear_history"),
                InlineKeyboardButton("📊 آمار دقیق", callback_data="detailed_stats")
            ],
            [
                InlineKeyboardButton("😄 حالت دوستانه", callback_data="mode_friendly"),
                InlineKeyboardButton("🎯 حالت جدی", callback_data="mode_serious"),
                InlineKeyboardButton("😈 حالت شیطون", callback_data="mode_naughty")
            ],
            [
                InlineKeyboardButton("❌ بستن منو", callback_data="close_menu")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚙️ *پنل تنظیمات روکسی*\n\n"
            "فقط شما به عنوان مالک گروه دسترسی دارید:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "clear_history":
            self.memory.clear_history(update.effective_chat.id)
            await query.edit_message_text("✅ حافظه گروه با موفقیت پاک شد!")
        
        elif query.data == "detailed_stats":
            stats = self.memory.get_stats(update.effective_chat.id)
            await query.edit_message_text(
                f"📊 *آمار دقیق گروه:*\n\n"
                f"• پیام‌های کل: {stats['total_messages']}\n"
                f"• پیام‌های کاربران: {stats['user_messages']}\n"
                f"• پاسخ‌های من: {stats['bot_messages']}\n"
                f"• مدل: {Config.MODEL}\n"
                f"• حافظه: {Config.MAX_HISTORY} پیام",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif query.data.startswith("mode_"):
            mode = query.data.replace("mode_", "")
            mode_names = {
                'friendly': 'دوستانه و مهربون 😊',
                'serious': 'جدی و حرفه‌ای 🎯',
                'naughty': 'شیطون و پررو 😈'
            }
            self.moods[update.effective_chat.id] = mode
            await query.edit_message_text(f"✅ حالت ربات تغییر کرد به: {mode_names.get(mode, mode)}")
        
        elif query.data == "close_menu":
            await query.edit_message_text("❌ منو بسته شد.")
    
    # --- هندلر پیام‌های عمومی ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_name = update.effective_user.first_name or "دوست عزیز"
        user_text = update.message.text.strip()
        
        # بررسی rate limit
        if self.is_rate_limited(user_id, limit=15, window=60):
            await update.message.reply_text(
                "🚫 آروم‌تر! خیلی سریع داری پیام می‌فرستی. یه نفس عمیق بکش 😊"
            )
            return
        
        # تشخیص اشاره به ربات
        bot_username = (await context.bot.get_me()).username.lower()
        is_reply_to_bot = (
            update.message.reply_to_message and
            update.message.reply_to_message.from_user.id == context.bot.id
        )
        mentions_bot = any(
            kw in user_text.lower()
            for kw in ["روکسی", "roxie", f"@{bot_username}", "ربات"]
        )
        
        # فیلتر پاسخ در گروه‌ها
        is_group = chat_type in ['group', 'supergroup']
        if is_group and not is_reply_to_bot and not mentions_bot:
            # فقط 25% مواقع پاسخ می‌دهد تا اسپم نشود
            if random.random() > 0.25:
                return
        
        # نشان دادن وضعیت typing
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        # تأخیر تصادفی برای طبیعی‌تر بودن (انسان‌ها فوری تایپ نمی‌کنند!)
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # تولید پاسخ
        response = await self.generate_response(
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            text=user_text,
            is_mentioned=mentions_bot or is_reply_to_bot
        )
        
        # ارسال پاسخ
        if is_reply_to_bot:
            await update.message.reply_text(response)
        else:
            # در چت خصوصی یا وقتی اشاره شده، ریپلای کن
            if not is_group or mentions_bot:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=response,
                    reply_to_message_id=update.message.message_id
                )
            else:
                await context.bot.send_message(chat_id=chat_id, text=response)
    
    # --- هندلر اعضای جدید ---
    async def welcome_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.new_chat_members:
            return
        
        for member in update.message.new_chat_members:
            if member.is_bot:
                continue
            
            welcome_text = (
                f"سلام {member.first_name} عزیز! 👋\n\n"
                f"خوش اومدی به جمع ما! 🎉\n"
                f"من **روکسی** هستم، اگه سوالی داشتی یا خواستی چت کنی، "
                f"اسمم رو صدا کن! 😊"
            )
            
            await update.message.reply_text(
                welcome_text,
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def left_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.left_chat_member:
            return
        
        member = update.message.left_chat_member
        if member.is_bot:
            return
        
        # با 50% احتمال پیام بدرقه بدهد
        if random.random() < 0.5:
            goodbye_text = (
                f"اوه {member.first_name} رفت! 👋\n"
                f"امیدوارم هرجا هستی موفق باشی 🌟"
            )
            await update.message.reply_text(goodbye_text)

# ============================================================
# 🚀 اجرای اصلی
# ============================================================
def main():
    if not Config.TELEGRAM_TOKEN or not Config.GROQ_API_KEY:
        print("\n" + "="*60)
        print("⚠️  خطا: توکن‌ها تنظیم نشده‌اند!")
        print("="*60)
        print("لطفاً فایل .env را ایجاد کنید:")
        print("  TELEGRAM_BOT_TOKEN=your_token")
        print("  GROQ_API_KEY=your_key")
        return
    
    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()
    
    # ثبت دستورات
    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("about", bot.about_command))
    app.add_handler(CommandHandler("clearhistory", bot.clear_history_command))
    app.add_handler(CommandHandler("stats", bot.stats_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("kick", bot.kick_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))
    app.add_handler(CommandHandler("warn", bot.warn_command))
    app.add_handler(CommandHandler("config", bot.config_command))
    
    # ثبت هندلرها
    app.add_handler(CallbackQueryHandler(bot.button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), bot.handle_message))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bot.welcome_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, bot.left_member))
    
    print("\n" + "="*60)
    print("🤖 روکسی با موفقیت راه‌اندازی شد!")
    print("="*60)
    print(f"📊 مدل: {Config.MODEL}")
    print(f"🧠 حافظه: {Config.MAX_HISTORY} پیام")
    print(f"🌡️ Temperature: {Config.TEMPERATURE}")
    print(f"🎲 Top P: {Config.TOP_P}")
    print("="*60 + "\n")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
