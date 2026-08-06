import os
import re
import json
import logging
import random
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from collections import defaultdict

from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus, ChatType, ChatAction
from groq import Groq, RateLimitError

load_dotenv()

# ============================================================
# ⚙️ تنظیمات
# ============================================================
class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8359090977:AAFhjvjY2ZiFqc0Kc3eWsXUqo2vjpXjlAgM").strip()
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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").strip()
    OWNER_ID = int(os.getenv("OWNER_ID", "993028263"))

    MAX_HISTORY = int(os.getenv("MAX_HISTORY", "5"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "250"))

    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "3"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
    TOP_P = float(os.getenv("TOP_P", "0.9"))

    MODEL = os.getenv("MODEL_NAME", "qwen/qwen3.6-27b")
    FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_NAME", "llama-3.1-8b-instant")
    REASONING_EFFORT = os.getenv("REASONING_EFFORT", "none")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    ATTITUDE_FILE = os.getenv("ATTITUDE_FILE", "roxie_attitude.json")
    MAX_MESSAGE_CHARS = 1000

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, Config.LOG_LEVEL),
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 پرامپت شخصیت روان و بدون غلط گرامری
# ============================================================
SYSTEM_PROMPT = """
You are Roxie (روکسی): a 20-year-old Iranian girl from Tehran texting on Telegram.

CRITICAL RULES FOR LANGUAGE & GRAMMAR:
1. Reply ONLY in natural, native, spoken Tehrani Persian (فارسی محاوره‌ای تهرانی).
2. ALWAYS use correct Persian grammar and proper verb endings. Say "دوستت دارم" or "عاشقتم", NEVER say "دوست‌داره‌ام" or "خیلی‌داره".
3. Keep replies EXTREMELY SHORT: exactly 1 natural sentence (max 10-12 words). Never write long paragraphs, never over-explain, never add random thoughts.
4. You are a real human girl. Never say you are an AI or bot.
5. Answer DIRECTLY to what the user just said based on your current feeling toward them (warm/neutral/cold).
   - Warm: Friendly, affectionate, playful
   - Neutral: Casual, cheeky, witty
   - Cold: Short, sharp, sarcastic
6. No name prefixes, no labels, no quotation marks, no markdown, max 1 emoji.
"""

# ============================================================
# ❤️ سیستم احساسات نسبت به هر کاربر
# ============================================================
class AttitudeTracker:
    POSITIVE_WORDS = [
        "دوستت دارم", "دوست دارم", "عاشقتم", "خوبی", "نازی", "ناز", "بامزه", "باحال",
        "باهوش", "خفن", "دمت گرم", "مرسی", "ممنون", "آفرین", "ایول", "قربونت",
        "عزیزی", "گلی", "ستون", "خوشگل", "قشنگی", "بهترینی", "عالی", "دوست‌داشتنی",
    ]
    NEGATIVE_WORDS = [
        "خنگ", "احمق", "مسخره", "چرت", "بیخود", "زشت", "خفه", "نفهم", "کسخل",
        "بیشعور", "بی‌شعور", "رو مخی", "رومخ", "رو اعصابی", "عوضی", "کثافت",
        "گمشو", "گم شو", "برو بابا", "افتضاح", "خفه شو",
    ]

    def __init__(self, path: str):
        self.path = path
        self.scores: Dict[int, int] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.scores = {int(k): int(v) for k, v in data.items()}
        except Exception:
            self.scores = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.scores, f)
        except Exception as e:
            logger.warning(f"Could not save attitude file: {e}")

    def observe(self, user_id: int, text: str):
        if not text: return
        lowered = text.lower()
        poshits = sum(1 for w in self.POSITIVE_WORDS if w in lowered)
        neghits = sum(1 for w in self.NEGATIVE_WORDS if w in lowered)
        delta = min(poshits, 2) - min(neghits, 2)
        if delta == 0: return
        current = self.scores.get(user_id, 0)
        new_score = max(-6, min(6, current + delta))
        if new_score != current:
            self.scores[user_id] = new_score
            self._save()

    def feeling(self, user_id: int) -> str:
        score = self.scores.get(user_id, 0)
        if score >= 2: return "صمیمی و دوست‌داشتنی"
        if score <= -2: return "سرد و بی‌محلی"
        return "معمولی"

# ============================================================
# 💾 مدیریت حافظه چت‌ها
# ============================================================
CONTEXT_MARKER = "[اطلاعات_گروه]"

class ChatMemory:
    def __init__(self, max_history: int = 5):
        self.histories: Dict[int, List[Dict[str, str]]] = defaultdict(list)
        self.max_history = max_history

    def ensure(self, chat_id: int) -> List[Dict[str, str]]:
        if chat_id not in self.histories:
            self.histories[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.histories[chat_id]

    def has_context(self, history: List[Dict[str, str]]) -> bool:
        return len(history) >= 2 and history[1]["content"].startswith(CONTEXT_MARKER)

    def set_context(self, chat_id: int, context_line: str):
        history = self.ensure(chat_id)
        entry = {"role": "system", "content": f"{CONTEXT_MARKER} {context_line}"}
        if self.has_context(history):
            if history[1]["content"] != entry["content"]:
                history[1] = entry
        else:
            history.insert(1, entry)

    def add_message(self, chat_id: int, role: str, content: str):
        history = self.ensure(chat_id)
        history.append({"role": role, "content": content})
        prefix_len = 2 if self.has_context(history) else 1
        max_items = prefix_len + self.max_history * 2
        if len(history) > max_items:
            self.histories[chat_id] = history[:prefix_len] + history[-(self.max_history * 2):]

    def get_history(self, chat_id: int) -> List[Dict[str, str]]:
        return self.ensure(chat_id)

    def clear_history(self, chat_id: int):
        old = self.histories.get(chat_id, [])
        context_entry = old[1] if self.has_context(old) else None
        fresh = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context_entry:
            fresh.append(context_entry)
        self.histories[chat_id] = fresh

# ============================================================
# 🎭 کلاس اصلی ربات
# ============================================================
class RoxieBot:
    def __init__(self):
        self.memory = ChatMemory(Config.MAX_HISTORY)
        self.attitude = AttitudeTracker(Config.ATTITUDE_FILE)
        self.groq_client = Groq(api_key=Config.GROQ_API_KEY)
        self.last_reply_time: Dict[int, datetime] = defaultdict(lambda: datetime.min)
        self.active_groups: Dict[int, str] = {}
        self.chat_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.bot_username: str = ""

    async def get_bot_username(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        if not self.bot_username:
            me = await context.bot.get_me()
            self.bot_username = (me.username or "").lower()
        return self.bot_username

    def is_cooling_down(self, chat_id: int) -> bool:
        elapsed = (datetime.now() - self.last_reply_time[chat_id]).total_seconds()
        return elapsed < Config.COOLDOWN_SECONDS

    def update_cooldown(self, chat_id: int):
        self.last_reply_time[chat_id] = datetime.now()

    async def is_admin(self, chat, user_id: int) -> bool:
        try:
            member = await chat.get_member(user_id)
            return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
        except Exception:
            return False

    async def get_role_tag(self, chat, chat_type: str, user_id: int) -> str:
        if user_id == Config.OWNER_ID:
            return "سازنده ربات (رئیس)"
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
            if await self.is_admin(chat, user_id):
                return "ادمین گروه"
        return "عضو عادی"

    def clean_response(self, text: str) -> str:
        if not text: return ""
        text = text.strip()
        text = re.sub(r"^[\s>*_«»\"\u201c\u201d]+", "", text)
        text = re.sub(r"^(?:roxie|roxy|روکسی)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*[:：]\s*", "", text)
        
        # اصلاح فیلتر افعال احتمالی اشتباه فارسی
        text = text.replace("دوست‌داره‌ام", "دوستت دارم").replace("خیلی‌داره", "خیلی دوستت دارم")
        
        return text.strip().strip('"').strip()

    def groq_request(self, model: str, history: List[Dict[str, str]]):
        kwargs = {
            "model": model,
            "messages": history,
            "temperature": Config.TEMPERATURE,
            "top_p": Config.TOP_P,
            "max_completion_tokens": Config.MAX_TOKENS,
            "stream": False,
        }
        # ارسال reasoning_effort فقط در صورت نیاز
        if Config.REASONING_EFFORT and Config.REASONING_EFFORT.lower() not in ["none", "", "default"]:
            if "gpt-oss" in model.lower():
                kwargs["reasoning_effort"] = Config.REASONING_EFFORT

        return self.groq_client.chat.completions.create(**kwargs)

    async def generate_reply(self, chat_id: int, new_messages: List[Dict[str, str]]) -> Optional[str]:
        async with self.chat_locks[chat_id]:
            for msg in new_messages:
                self.memory.add_message(chat_id, msg["role"], msg["content"])
            history = self.memory.get_history(chat_id)

            models_to_try = [Config.MODEL]
            if Config.FALLBACK_MODEL and Config.FALLBACK_MODEL != Config.MODEL:
                models_to_try.append(Config.FALLBACK_MODEL)

            for model in models_to_try:
                try:
                    completion = await asyncio.to_thread(self.groq_request, model, history)
                    choice = completion.choices[0]
                    raw_reply = choice.message.content or ""
                    reply = self.clean_response(raw_reply)
                    if reply:
                        self.memory.add_message(chat_id, "assistant", reply)
                        return reply
                except RateLimitError:
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Error on model {model}: {e}")
            return None

    async def reply_with_ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            event_text: str, username: str, role_tag: str, feeling: str) -> Optional[str]:
        tag = f"[رویداد | فرستنده: {username} | حس تو بهش: {feeling}]\n{event_text}"
        response = await self.generate_reply(update.effective_chat.id, [{"role": "user", "content": tag}])
        if response and update.message:
            await update.message.reply_text(response)
        elif response:
            await context.bot.send_message(update.effective_chat.id, response)
        return response

    # ---------- دستورات عمومی ----------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user = update.effective_user
        if chat.type == ChatType.PRIVATE and user.id != Config.OWNER_ID:
            return
        role_tag = await self.get_role_tag(chat, chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"
        await self.reply_with_ai(
            update, context,
            "این کاربر دستور /start فرستاد. خیلی کوتاه در ۱ جمله بهش سلام کن.",
            username, role_tag, feeling,
        )

    # ---------- دستورات مخصوص سازنده (993028263) ----------
    async def mygroups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        feeling = self.attitude.feeling(update.effective_user.id)
        if self.active_groups:
            lines = "\n".join(f"- {title} (ID: {gid})" for gid, title in self.active_groups.items())
            event = f"لیست گروه‌ها:\n{lines}\nخیلی کوتاه تحویلش بده."
        else:
            event = "توی هیچ گروهی نیستی. خیلی کوتاه بگو."
        await self.reply_with_ai(update, context, event, update.effective_user.first_name or "ناشناس", "سازنده ربات (رئیس)", feeling)

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID: return
        user_name = update.effective_user.first_name or "ناشناس"
        feeling = self.attitude.feeling(update.effective_user.id)
        if not context.args:
            await self.reply_with_ai(update, context, "آیدی گروه رو نگفت. بگو آیدی رو بفرسته.", user_name, "سازنده ربات (رئیس)", feeling)
            return
        try:
            target_chat_id = int(context.args[0])
            await context.bot.leave_chat(target_chat_id)
            self.active_groups.pop(target_chat_id, None)
            event = f"از گروه {target_chat_id} خارج شدی. ۱ جمله کوتاه بگو."
        except Exception:
            event = "خروج از گروه انجام نشد."
        await self.reply_with_ai(update, context, event, user_name, "سازنده ربات (رئیس)", feeling)

    # ---------- پردازش اصلی پیام‌ها ----------
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        if not message or not update.effective_user or not update.effective_chat: return
        user = update.effective_user
        chat = update.effective_chat
        chat_type = chat.type

        # قفل پیوی: فقط سازنده (993028263)
        if chat_type == ChatType.PRIVATE and user.id != Config.OWNER_ID:
            return

        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
            self.active_groups[chat.id] = chat.title or f"Group {chat.id}"
            self.memory.set_context(
                chat.id,
                f'این چت درون گروه تلگرامی "{chat.title}" انجام می‌شود.',
            )

        role_tag = await self.get_role_tag(chat, chat_type, user.id)
        username = user.first_name or "ناشناس"

        text = (message.text or message.caption or "").strip()

        media_note = ""
        if message.photo: media_note = "[فرستادن عکس]"
        elif message.animation: media_note = "[فرستادن گیف]"
        elif message.video: media_note = "[فرستادن ویدیو]"
        elif message.sticker: media_note = f"[فرستادن استیکر {message.sticker.emoji or ''}]".strip()

        quoted_part = ""
        if message.reply_to_message:
            replied = message.reply_to_message
            quoted_text = (replied.text or replied.caption or "").strip()
            if quoted_text:
                quoted_name = replied.from_user.first_name if replied.from_user else "?"
                quoted_part = f'[پیام ریپلای‌شده از {quoted_name}]: "{quoted_text[:200]}"'

        user_text = "\n".join(part for part in (quoted_part, media_note, text) if part).strip()
        if not user_text: return
        if len(user_text) > Config.MAX_MESSAGE_CHARS:
            user_text = user_text[: Config.MAX_MESSAGE_CHARS] + "…"

        self.attitude.observe(user.id, text)
        feeling = self.attitude.feeling(user.id)

        # بن هوشمند بدون دستور (فقط ادمین/مالک روی ریپلای)
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP) and message.reply_to_message:
            ban_phrases = ["بنش کن", "بن کن", "بندازش بیرون", "اخراجش کن", "شوتش کن", "دیلیتش کن", "بکنش بیرون"]
            if role_tag in ("سازنده ربات (رئیس)", "ادمین گروه") and any(p in text.lower() for p in ban_phrases):
                target = message.reply_to_message.from_user
                if target and target.id != user.id and target.id != context.bot.id:
                    try:
                        await context.bot.ban_chat_member(chat.id, target.id)
                        await self.reply_with_ai(
                            update, context,
                            f"کاربر «{target.first_name}» را بن کردی. یک خط کوتاه بگو.",
                            username, role_tag, feeling,
                        )
                        return
                    except Exception as e:
                        logger.error(f"Smart ban failed: {e}")

        # شرط صحبت در گروه: فقط ریپلای، منشن یا اسم "روکسی"
        bot_username = await self.get_bot_username(context)
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        name_triggers = ["روکسی", "roxie", "راکسی"]
        if bot_username: name_triggers.append(f"@{bot_username}")
        mentions_bot = any(word in text.lower() for word in name_triggers)

        is_group = chat_type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        if self.is_cooling_down(chat.id): return
        self.update_cooldown(chat_id=chat.id)

        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # فرمت تمیز پیام کاربر
        formatted_message = f"{user_text}"
        
        response = await self.generate_reply(chat.id, [{"role": "user", "content": formatted_message}])
        if not response: return

        if chat_type == ChatType.PRIVATE or is_reply_to_bot or mentions_bot:
            await message.reply_text(response)
        else:
            await context.bot.send_message(chat_id=chat.id, text=response)

# ============================================================
# 🚀 اجرا
# ============================================================
def main():
    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("mygroups", bot.mygroups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), bot.handle_message))

    print("🦊 Roxie with fixed reasoning_effort is up!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
