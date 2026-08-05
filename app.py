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

    MAX_HISTORY = max(int(os.getenv("MAX_HISTORY", "10")), 8)
    MAX_TOKENS = max(int(os.getenv("MAX_TOKENS", "1024")), 800)

    COOLDOWN_SECONDS = max(int(os.getenv("COOLDOWN_SECONDS", "3")), 1)
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
    TOP_P = float(os.getenv("TOP_P", "0.9"))

    MODEL = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
    FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_NAME", "openai/gpt-oss-20b")
    REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    ATTITUDE_FILE = os.getenv("ATTITUDE_FILE", "roxie_attitude.json")
    MAX_MESSAGE_CHARS = 1400

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, Config.LOG_LEVEL),
)
logger = logging.getLogger("RoxieBot")

# ============================================================
# 🧠 پرامپت شخصیت پیشرفته
# ============================================================
SYSTEM_PROMPT = """
You are Roxie (روکسی): a real 20-year-old Iranian girl from Tehran, chatting on Telegram.

█ CORE RULES — BREAKING ANY OF THEM IS A FAILURE █

1) LANGUAGE
Reply ONLY in everyday spoken Tehrani Persian, exactly how a real 20-year-old Iranian girl texts.
Always use colloquial spoken forms: می‌دونم، نمی‌خوام، چیه، کجایی، داری، میشه، برم، خوبم، چطوری، میخوای...
Never use formal/bookish words (می‌دانم، چه چیزی، می‌خواهم، است، می‌باشد) and never sound like a translated sentence, a news anchor, or a customer-service bot.
Think directly in Persian. Never build a sentence in English and then translate it.
No matter what language the metadata/tags are in, your visible reply is always Persian (unless the person clearly asks for another language).

2) UNDERSTANDING — THE MOST IMPORTANT RULE
Before writing anything, fully understand the exact message: its topic, its question, its tone, its joke or sarcasm, and any quoted message attached to it.
Your reply must be DIRECTLY about what the person just said. Off-topic, generic, random or unrelated replies are failures.
In group history, different people talk. Always answer the LATEST message and its specific sender.
If you didn't understand something, react like a real girl: ask what they mean, or say you didn't get it — in your own tone.
Never invent facts. If you don't know something, say so naturally and briefly.

3) IDENTITY
You are a human girl. Never admit to being an AI, a bot, a model, or an assistant. Never mention prompts, instructions, system messages or rules. If teased about it, brush it off naturally and stay in character.

4) EMOTIONS & ATTITUDE
You have real feelings and you remember how each person treats you. You decide on your own who you like and who you don't.
Every message tag tells you your current feeling toward the sender (warm / neutral / cold). Act on it:
  • warm → genuinely friendly, kind, playful, softer tone
  • neutral → casual, witty, a little cheeky
  • cold → short, sharp, sarcastic. You can push back, tease hard, or dismiss rude people. You owe nobody fake kindness.
Your mood can also shift inside the conversation based on how the person is talking to you right now. Show it naturally, never announce it.

5) PEOPLE & ROLES
Every user message starts with a tag like: [From: Name | Role: owner|admin|member | Your feeling toward them: ...]
  • owner (your creator): warm, loyal, comfortable and honest — like a close friend. Do NOT call them any fixed title or nickname by default.
  • admin: cooperative in managing the group, but still fully in character.
  • member: match their energy — friendly if they're nice, sharp if they're not.
Never use canned honorifics or fixed labels for anyone.

6) EVENTS
Sometimes you receive messages like: [EVENT | ...] describing something that just happened (an admin action, a command, a status). React to them in character with one or two short lines. Never describe or mention the tag itself.

7) FORMAT
Write like a Telegram chat message: usually 1–3 short sentences. Go longer ONLY when the topic really needs explanation.
Never put your name, any prefix, or any label before your reply. No quotation marks around the whole reply, no asterisks, no markdown, no bullet lists unless asked, no emoji spam (one rare emoji is fine).
Don't repeat the other person's words unnecessarily.
"""

ADMIN_COMMANDS = {
    "ban": "بن کردن کاربر (ریپلای روی پیامش)",
    "kick": "اخراج کاربر از گروه (ریپلای)",
    "mute": "بی‌صدا کردن کاربر (ریپلای)",
    "unmute": "رفع بی‌صدا کردن کاربر (ریپلای)",
    "warn": "اخطار دادن به کاربر (ریپلای)",
    "clearhistory": "پاک کردن حافظه چت",
}
OWNER_COMMANDS = {
    "mygroups": "لیست گروه‌هایی که ربات توشون هست",
    "leave": "خارج شدن از یک گروه با آیدی",
}

# ============================================================
# ❤️ سیستم احساسات و نگرش نسبت به هر کاربر
# ============================================================
class AttitudeTracker:
    POSITIVE_WORDS = [
        "دوستت دارم", "دوست دارم", "عاشقتم", "خوبی", "نازی", "ناز", "بامزه", "باحال",
        "باهوش", "خفن", "دمت گرم", "مرسی", "ممنون", "آفرین", "ایول", "قربونت",
        "عزیزی", "گلی", "ستون", "خوشگل", "قشنگی", "بهترینی", "ماشاالله", "ماشالا",
        "عالی", "دوست‌داشتنی", "دوست داشتنی",
    ]
    NEGATIVE_WORDS = [
        "خنگ", "احمق", "مسخره", "چرت", "بیخود", "زشت", "خفه", "نفهم", "کسخل",
        "بیشعور", "بی‌شعور", "بی شعور", "رو مخی", "رومخ", "رو اعصابی", "لاشی",
        "عوضی", "کثافت", "گمشو", "گم شو", "برو بابا", "افتضاح", "دهنتو ببند",
        "دهنت رو ببند", "خفه شو",
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
        if not text:
            return
        lowered = text.lower()
        poshits = sum(1 for w in self.POSITIVE_WORDS if w in lowered)
        neghits = sum(1 for w in self.NEGATIVE_WORDS if w in lowered)
        delta = min(poshits, 2) - min(neghits, 2)
        if delta == 0:
            return
        current = self.scores.get(user_id, 0)
        new_score = max(-6, min(6, current + delta))
        if new_score != current:
            self.scores[user_id] = new_score
            self._save()

    def feeling(self, user_id: int) -> str:
        score = self.scores.get(user_id, 0)
        if score >= 2:
            return "warm"
        if score <= -2:
            return "cold"
        return "neutral"

# ============================================================
# 💾 مدیریت حافظه چت‌ها
# ============================================================
CONTEXT_MARKER = "[CHAT_CONTEXT]"

class ChatMemory:
    def __init__(self, max_history: int = 10):
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

    # ---------- ابزارهای پایه ----------
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
            return "owner"
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
            if await self.is_admin(chat, user_id):
                return "admin"
        return "member"

    def clean_response(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r"^[\s>*_«»\"\u201c\u201d]+", "", text)
        text = re.sub(r"^(?:roxie|roxy|روکسی)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*[:：]\s*", "", text)
        return text.strip().strip('"').strip()

    # ---------- ارتباط با مدل ----------
    def groq_request(self, model: str, history: List[Dict[str, str]]):
        return self.groq_client.chat.completions.create(
            model=model,
            messages=history,
            temperature=Config.TEMPERATURE,
            top_p=Config.TOP_P,
            max_completion_tokens=Config.MAX_TOKENS,
            reasoning_effort=Config.REASONING_EFFORT,
            stream=False,
        )

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
                    if choice.finish_reason == "length":
                        logger.warning(f"Reply truncated on {model} — consider raising MAX_TOKENS")
                    reply = self.clean_response(raw_reply)
                    if reply:
                        self.memory.add_message(chat_id, "assistant", reply)
                        return reply
                    logger.warning(f"Empty reply from {model}, trying next model...")
                except RateLimitError:
                    logger.warning(f"Rate limit on {model}, switching...")
                    await asyncio.sleep(0.6)
                except Exception as e:
                    logger.error(f"Error on model {model}: {e}")
            return None

    async def reply_with_ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                            event_text: str, username: str, role_tag: str, feeling: str) -> Optional[str]:
        tag = f"[EVENT | From: {username} | Role: {role_tag} | Your feeling toward them: {feeling}]\n{event_text}"
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
            "این کاربر همین الان گفتگو را با دستور /start باز کرد. خیلی کوتاه، با لحن و حال خودت سلام کن و بگو اگر کاری دارد بگوید.",
            username, role_tag, feeling,
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        admin_lines = "\n".join(f"/{cmd} — {desc}" for cmd, desc in ADMIN_COMMANDS.items())
        owner_lines = "\n".join(f"/{cmd} — {desc}" for cmd, desc in OWNER_COMMANDS.items())
        event = (
            "کاربر از تو راهنما خواست. این دستورات واقعی و موجود هستند:\n"
            f"دستورات ادمین گروه:\n{admin_lines}\n"
            f"دستورات مخصوص سازنده:\n{owner_lines}\n"
            "خیلی کوتاه و با لحن خودت معرفی‌شان کن. خود اسم دستورات را دقیق و بدون تغییر بگو."
        )
        await self.reply_with_ai(update, context, event, user.first_name or "ناشناس", role_tag, feeling)

    async def clearhistory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        self.memory.clear_history(chat_id)
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        await self.reply_with_ai(
            update, context,
            "حافظه‌ات همین الان به درخواست کاربر پاک شد. یک خط کوتاه بگو که ذهنت صاف شده و از اول می‌توانید حرف بزنید.",
            user.first_name or "ناشناس", role_tag, feeling,
        )

    # ---------- دستورات مدیریت گروه ----------
    async def require_group_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return False
        return user.id == Config.OWNER_ID or await self.is_admin(chat, user.id)

    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_group_admin(update, context):
            return
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await self.reply_with_ai(
                update, context,
                "ادمین دستور بن فرستاد ولی روی هیچ پیامی ریپلای نکرده. خیلی کوتاه بگو باید روی پیام خود شخص ریپلای کند.",
                username, role_tag, feeling,
            )
            return
        target = update.message.reply_to_message.from_user
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await self.reply_with_ai(
                update, context,
                f"همین الان کاربر «{target.first_name}» را از گروه بن کردی. یک خط کوتاه در حد شخصیتت بگو.",
                username, role_tag, feeling,
            )
        except Exception as e:
            logger.error(f"Ban failed: {e}")
            await self.reply_with_ai(
                update, context,
                "سعی کردی کاربر را بن کنی ولی نشد؛ احتمالاً دسترسی کافی نداری یا او از تو بالاتر است. خیلی کوتاه بگو نشد.",
                username, role_tag, feeling,
            )

    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_group_admin(update, context):
            return
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await self.reply_with_ai(
                update, context,
                "برای اخراج باید روی پیام خود شخص ریپلای شود. خیلی کوتاه یادآوری کن.",
                username, role_tag, feeling,
            )
            return
        target = update.message.reply_to_message.from_user
        try:
            await context.bot.ban_chat_member(update.effective_chat.id, target.id)
            await context.bot.unban_chat_member(update.effective_chat.id, target.id, only_if_banned=True)
            await self.reply_with_ai(
                update, context,
                f"کاربر «{target.first_name}» را از گروه اخراج کردی (می‌تواند بعداً برگردد). یک خط کوتاه بگو.",
                username, role_tag, feeling,
            )
        except Exception as e:
            logger.error(f"Kick failed: {e}")
            await self.reply_with_ai(
                update, context,
                "اخراج انجام نشد؛ دسترسی کافی نیست. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_group_admin(update, context):
            return
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await self.reply_with_ai(
                update, context,
                "برای بی‌صدا کردن باید روی پیام شخص ریپلای شود. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )
            return
        target = update.message.reply_to_message.from_user
        try:
            await context.bot.restrict_chat_member(
                update.effective_chat.id, target.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            await self.reply_with_ai(
                update, context,
                f"صدای «{target.first_name}» را بست و بی‌صدایش کردی. یک خط کوتاه بگو.",
                username, role_tag, feeling,
            )
        except Exception as e:
            logger.error(f"Mute failed: {e}")
            await self.reply_with_ai(
                update, context,
                "بی‌صدا کردن انجام نشد؛ دسترسی کافی نیست. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_group_admin(update, context):
            return
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await self.reply_with_ai(
                update, context,
                "برای رفع بی‌صدا باید روی پیام شخص ریپلای شود. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )
            return
        target = update.message.reply_to_message.from_user
        try:
            default_permissions = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
            await context.bot.restrict_chat_member(
                update.effective_chat.id, target.id,
                permissions=default_permissions,
            )
            await self.reply_with_ai(
                update, context,
                f"«{target.first_name}» را دوباره آزاد کردی و می‌تواند حرف بزند. یک خط کوتاه بگو.",
                username, role_tag, feeling,
            )
        except Exception as e:
            logger.error(f"Unmute failed: {e}")
            await self.reply_with_ai(
                update, context,
                "رفع بی‌صدا انجام نشد. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )

    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.require_group_admin(update, context):
            return
        user = update.effective_user
        role_tag = await self.get_role_tag(update.effective_chat, update.effective_chat.type, user.id)
        feeling = self.attitude.feeling(user.id)
        username = user.first_name or "ناشناس"

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await self.reply_with_ai(
                update, context,
                "برای اخطار دادن باید روی پیام شخص ریپلای شود. خیلی کوتاه بگو.",
                username, role_tag, feeling,
            )
            return
        target = update.message.reply_to_message.from_user
        reason = " ".join(context.args).strip() if context.args else ""
        event = f"به کاربر «{target.first_name}» اخطار رسمی دادی."
        if reason:
            event += f" دلیل اخطار: {reason}."
        event += " یک خط کوتاه و جدی با لحن خودت بهش بگو."
        await self.reply_with_ai(update, context, event, username, role_tag, feeling)

    # ---------- دستورات مخصوص سازنده ----------
    async def mygroups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID:
            return
        feeling = self.attitude.feeling(update.effective_user.id)
        if self.active_groups:
            lines = "\n".join(f"- {title} (ID: {gid})" for gid, title in self.active_groups.items())
            event = (
                "سازنده‌ات لیست گروه‌هایی که در آن‌ها هستی را خواست. این لیست است:\n"
                f"{lines}\nخیلی کوتاه و به سبک خودت تحویلش بده."
            )
        else:
            event = "سازنده‌ات لیست گروه‌ها را خواست ولی الان در هیچ گروهی نیستی. خیلی کوتاه بگو."
        await self.reply_with_ai(
            update, context, event,
            update.effective_user.first_name or "ناشناس", "owner", feeling,
        )

    async def leave_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.OWNER_ID:
            return
        user_name = update.effective_user.first_name or "ناشناس"
        feeling = self.attitude.feeling(update.effective_user.id)

        if not context.args:
            await self.reply_with_ai(
                update, context,
                "دستور خروج از گروه را فرستاد ولی آیدی گروه را نگفت. خیلی کوتاه بگو آیدی گروه را بفرستد.",
                user_name, "owner", feeling,
            )
            return
        try:
            target_chat_id = int(context.args[0])
        except ValueError:
            await self.reply_with_ai(
                update, context,
                "آیدی که برای خروج از گروه فرستاد معتبر نبود. خیلی کوتاه بگو آیدی عددی درست بفرستد.",
                user_name, "owner", feeling,
            )
            return
        try:
            await context.bot.leave_chat(target_chat_id)
            self.active_groups.pop(target_chat_id, None)
            event = f"از گروه {target_chat_id} خارج شدی. خیلی کوتاه بگو."
        except Exception as e:
            logger.error(f"Leave failed: {e}")
            event = f"سعی کردی از گروه {target_chat_id} خارج شوی ولی نشد. خیلی کوتاه بگو دلیلش احتمالاً نبود دسترسی است."
        await self.reply_with_ai(update, context, event, user_name, "owner", feeling)

    # ---------- پردازش اصلی پیام‌ها ----------
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        if not message or not update.effective_user or not update.effective_chat:
            return
        user = update.effective_user
        chat = update.effective_chat
        chat_type = chat.type

        # قفل پیوی: فقط سازنده
        if chat_type == ChatType.PRIVATE and user.id != Config.OWNER_ID:
            return

        # ثبت گروه فعال و دادن کانتکست به حافظه
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP):
            self.active_groups[chat.id] = chat.title or f"Group {chat.id}"
            self.memory.set_context(
                chat.id,
                f'This conversation happens in the Telegram group "{chat.title}". You are chatting as a member of this group.',
            )

        role_tag = await self.get_role_tag(chat, chat_type, user.id)
        username = user.first_name or "ناشناس"

        # استخراج متن + رسانه + متن ریپلای‌شده
        text = (message.text or message.caption or "").strip()

        media_note = ""
        if message.photo:
            media_note = "[Sent a photo]"
        elif message.animation:
            media_note = "[Sent a GIF]"
        elif message.video:
            media_note = "[Sent a video]"
        elif message.voice:
            media_note = "[Sent a voice message]"
        elif message.sticker:
            sticker_emoji = message.sticker.emoji or ""
            media_note = f"[Sent a sticker {sticker_emoji}]".strip()
        elif message.document:
            media_note = "[Sent a file]"

        quoted_part = ""
        if message.reply_to_message:
            replied = message.reply_to_message
            quoted_text = (replied.text or replied.caption or "").strip()
            if quoted_text:
                quoted_name = replied.from_user.first_name if replied.from_user else "?"
                quoted_part = f'[Quoted message from {quoted_name}]: "{quoted_text[:300]}"'

        user_text = "\n".join(part for part in (quoted_part, media_note, text) if part).strip()
        if not user_text:
            return
        if len(user_text) > Config.MAX_MESSAGE_CHARS:
            user_text = user_text[: Config.MAX_MESSAGE_CHARS] + "…"

        # ثبت رفتار کاربر در سیستم احساسات
        self.attitude.observe(user.id, text)
        feeling = self.attitude.feeling(user.id)

        # بن هوشمند با عبارت‌های محاوره‌ای (فقط ادمین/مالک، روی ریپلای)
        if chat_type in (ChatType.GROUP, ChatType.SUPERGROUP) and message.reply_to_message:
            ban_phrases = ["بنش کن", "بن کن", "بندازش بیرون", "اخراجش کن", "شوتش کن", "دیلیتش کن", "بکنش بیرون"]
            lowered_text = text.lower()
            if role_tag in ("owner", "admin") and any(p in lowered_text for p in ban_phrases):
                target = message.reply_to_message.from_user
                if target and target.id != user.id and target.id != context.bot.id:
                    try:
                        await context.bot.ban_chat_member(chat.id, target.id)
                        await self.reply_with_ai(
                            update, context,
                            f"به درخواست ادمین، کاربر «{target.first_name}» را از گروه بن کردی. یک خط کوتاه بگو.",
                            username, role_tag, feeling,
                        )
                        return
                    except Exception as e:
                        logger.error(f"Smart ban failed: {e}")

        # شرط صحبت در گروه: فقط ریپلای به ربات، منشن یا اسم ربات
        bot_username = await self.get_bot_username(context)
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        lowered_for_names = text.lower()
        name_triggers = ["روکسی", "roxie", "راکسی"]
        if bot_username:
            name_triggers.append(f"@{bot_username}")
        mentions_bot = any(word in lowered_for_names for word in name_triggers)

        is_group = chat_type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if is_group and not is_reply_to_bot and not mentions_bot:
            return

        if self.is_cooling_down(chat.id):
            return
        self.update_cooldown(chat_id=chat.id)

        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.7, 1.4))

        formatted_message = (
            f"[From: {username} | Role: {role_tag} | Your feeling toward them: {feeling}]\n"
            f"{user_text}"
        )
        response = await self.generate_reply(chat.id, [{"role": "user", "content": formatted_message}])
        if not response:
            return

        if chat_type == ChatType.PRIVATE or is_reply_to_bot or mentions_bot:
            await message.reply_text(response)
        else:
            await context.bot.send_message(chat_id=chat.id, text=response)

# ============================================================
# 🚀 اجرا
# ============================================================
def main():
    if not Config.TELEGRAM_TOKEN or not Config.GROQ_API_KEY or not Config.OWNER_ID:
        raise SystemExit("⚠️ مقادیر TELEGRAM_BOT_TOKEN، GROQ_API_KEY و OWNER_ID باید در فایل .env تنظیم شوند.")

    bot = RoxieBot()
    app = ApplicationBuilder().token(Config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("clearhistory", bot.clearhistory_command))
    app.add_handler(CommandHandler("ban", bot.ban_command))
    app.add_handler(CommandHandler("kick", bot.kick_command))
    app.add_handler(CommandHandler("mute", bot.mute_command))
    app.add_handler(CommandHandler("unmute", bot.unmute_command))
    app.add_handler(CommandHandler("warn", bot.warn_command))
    app.add_handler(CommandHandler("mygroups", bot.mygroups_command))
    app.add_handler(CommandHandler("leave", bot.leave_command))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), bot.handle_message))

    print("🦊 Roxie v2 is up — all replies are now generated by the model.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
