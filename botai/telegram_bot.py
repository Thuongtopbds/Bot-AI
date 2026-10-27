"""Mỗi trợ lý = 1 bot Telegram (token riêng từ BotFather). Tất cả chạy chung một tiến trình."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from telegram import Message, Update
from telegram.constants import ChatAction, ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import Agent, Team
from .auth import AccessControl
from .tools import ToolContext

log = logging.getLogger(__name__)

TG_LIMIT = 4000
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}

HELP = """Mình là {name}.
Gửi tin nhắn, video, ảnh, file hoặc vị trí để giao việc.
Lệnh:
/reset – xóa ngữ cảnh, bắt đầu việc mới
/status – xem trạng thái & token đã dùng
/id – xem Telegram ID của bạn
/approve MÃ – (admin) duyệt người dùng mới"""


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return name[:80] or "file"


class TelegramAgentBot:
    def __init__(self, agent: Agent, team: Team, access: AccessControl):
        self.agent = agent
        self.team = team
        self.access = access
        self.settings = team.settings
        builder = Application.builder().token(agent.cfg.telegram_token)
        if self.settings.telegram_api_base:
            # Bot API server tự host: cho phép nhận file lớn hơn 20MB
            base = self.settings.telegram_api_base.rstrip("/")
            builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(True)
        builder = builder.read_timeout(120).write_timeout(600).media_write_timeout(600)
        self.app: Application = builder.build()
        self.username: str | None = None
        self.app.add_handler(CommandHandler(["start", "help"], self.cmd_help))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("id", self.cmd_id))
        self.app.add_handler(CommandHandler("approve", self.cmd_approve))
        self.app.add_handler(MessageHandler(~filters.COMMAND, self.on_message))

    # -- vòng đời -----------------------------------------------------------
    async def start(self) -> None:
        await self.app.initialize()
        me = await self.app.bot.get_me()
        self.username = me.username
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=False)
        log.info("Bot @%s (%s) đã chạy", me.username, self.agent.cfg.name)

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    # -- tiện ích ------------------------------------------------------------
    def session_key(self, update: Update) -> str:
        return str(update.effective_chat.id)

    async def send_text(self, chat_id: int, text: str) -> None:
        for chunk in split_text(text):
            await self.app.bot.send_message(chat_id, chunk)

    async def send_file(self, chat_id: int, path: Path, caption: str = "") -> None:
        caption = caption[:1000]
        ext = path.suffix.lower()
        with path.open("rb") as f:
            if ext in VIDEO_EXT:
                await self.app.bot.send_video(chat_id, f, caption=caption, supports_streaming=True)
            elif ext in PHOTO_EXT:
                await self.app.bot.send_photo(chat_id, f, caption=caption)
            else:
                await self.app.bot.send_document(chat_id, f, caption=caption)

    def make_ctx(self, chat_id: int) -> ToolContext:
        async def send_file(path: Path, caption: str) -> None:
            await self.send_file(chat_id, path, caption)

        async def notify(text: str) -> None:
            await self.send_text(chat_id, text)

        return ToolContext(
            agent=self.agent.cfg, settings=self.settings, chat_id=chat_id,
            workdir=self.team.workdir(chat_id), send_file=send_file, notify=notify,
        )

    async def check_access(self, update: Update) -> bool:
        user = update.effective_user
        if user is None:
            return False
        if self.access.is_allowed(user.id):
            return True
        if update.effective_chat.type == ChatType.PRIVATE:
            code = self.access.request_code(user.id, user.full_name)
            await update.effective_message.reply_text(
                f"Xin chào {user.first_name}! Bạn chưa được cấp quyền.\n"
                f"Mã kết nối của bạn: {code}\n\n"
                f"Chủ bot duyệt bằng lệnh trên máy chủ:\n  python -m botai approve {code}\n"
                f"hoặc gửi trong Telegram:  /approve {code}"
            )
        return False

    def addressed_to_me(self, msg: Message) -> bool:
        """Trong group: chỉ trả lời khi được @nhắc tên hoặc trả lời (reply) tin nhắn của bot."""
        if msg.chat.type == ChatType.PRIVATE:
            return True
        if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.username == self.username:
            return True
        text = (msg.text or msg.caption or "").lower()
        return bool(self.username) and f"@{self.username.lower()}" in text

    # -- lệnh ------------------------------------------------------------------
    async def cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.check_access(update):
            await update.effective_message.reply_text(HELP.format(name=self.agent.cfg.display_name))

    async def cmd_id(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            f"User ID: {update.effective_user.id}\nChat ID: {update.effective_chat.id}"
        )

    async def cmd_reset(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.check_access(update):
            return
        s = self.agent.session(self.session_key(update))
        if s.busy:
            await update.effective_message.reply_text("Đang làm dở việc, chờ xong rồi /reset nhé.")
            return
        self.agent.reset(self.session_key(update))
        await update.effective_message.reply_text("Đã xóa ngữ cảnh. Bắt đầu việc mới!")

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.check_access(update):
            return
        s = self.agent.session(self.session_key(update))
        cfg = self.agent.cfg
        await update.effective_message.reply_text(
            f"{cfg.display_name} | {cfg.provider}:{cfg.model}\n"
            f"Trạng thái: {'đang làm việc' if s.busy else 'rảnh'}\n"
            f"Tin nhắn trong ngữ cảnh: {len(s.history)}\n"
            f"Token phiên này: vào {s.usage.input_tokens:,} / ra {s.usage.output_tokens:,}\n"
            f"Kỹ năng: {', '.join(self.agent.tools)}"
        )

    async def cmd_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.access.is_admin(update.effective_user.id):
            await update.effective_message.reply_text("Chỉ admin (ADMIN_TELEGRAM_IDS) mới duyệt được.")
            return
        if not context.args:
            await update.effective_message.reply_text("Cú pháp: /approve MÃ")
            return
        entry = self.access.approve(context.args[0])
        if entry:
            await update.effective_message.reply_text(f"Đã duyệt {entry['name']} (id {entry['user_id']}).")
        else:
            await update.effective_message.reply_text("Mã không đúng hoặc đã hết hạn.")

    # -- tin nhắn ----------------------------------------------------------------
    async def download(self, msg: Message, workdir: Path) -> tuple[list[str], list[Path]]:
        """Tải file đính kèm về inbox/. Trả về (mô tả cho AI, danh sách ảnh để AI nhìn)."""
        notes: list[str] = []
        images: list[Path] = []
        inbox = workdir / "inbox"
        stamp = time.strftime("%H%M%S")
        target = None
        if msg.photo:
            target, name, kind = msg.photo[-1], f"photo_{stamp}_{msg.message_id}.jpg", "ảnh"
        elif msg.video:
            target, name, kind = msg.video, msg.video.file_name or f"video_{stamp}_{msg.message_id}.mp4", "video"
        elif msg.document:
            target, name, kind = msg.document, msg.document.file_name or f"file_{stamp}", "file"
        elif msg.audio:
            target, name, kind = msg.audio, msg.audio.file_name or f"audio_{stamp}.mp3", "âm thanh"
        elif msg.voice:
            target, name, kind = msg.voice, f"voice_{stamp}_{msg.message_id}.ogg", "ghi âm"
        if target is not None:
            size_mb = (target.file_size or 0) / 1_048_576
            if size_mb > self.settings.max_upload_mb and not self.settings.telegram_api_base:
                notes.append(f"[Người dùng gửi {kind} {size_mb:.0f}MB nhưng vượt giới hạn tải {self.settings.max_upload_mb}MB của Telegram Bot API]")
                return notes, images
            path = inbox / safe_name(name)
            if path.exists():
                path = path.with_name(f"{path.stem}_{msg.message_id}{path.suffix}")
            tg_file = await target.get_file()
            await tg_file.download_to_drive(custom_path=path)
            notes.append(f"[Đính kèm {kind}: {path.relative_to(workdir)} ({size_mb:.1f} MB)]")
            if path.suffix.lower() in PHOTO_EXT and self.agent.cfg.vision:
                images.append(path)
        if msg.location:
            loc = msg.location
            notes.append(f"[Người dùng gửi vị trí: lat={loc.latitude}, lon={loc.longitude}]")
        if msg.venue:
            notes.append(f"[Địa điểm: {msg.venue.title} — {msg.venue.address}]")
        return notes, images

    async def on_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if msg is None or not await self.check_access(update) or not self.addressed_to_me(msg):
            return
        chat_id = update.effective_chat.id
        workdir = self.team.workdir(chat_id)
        try:
            notes, images = await self.download(msg, workdir)
        except (BadRequest, TelegramError) as e:
            await msg.reply_text(f"Không tải được file: {e}. File >20MB cần cấu hình TELEGRAM_API_BASE (xem hướng dẫn).")
            return
        text = (msg.text or msg.caption or "").strip()
        if self.username:
            text = text.replace(f"@{self.username}", "").strip()
        sender = update.effective_user.full_name
        full = "\n".join([f"{sender}: {text}" if text else f"{sender} gửi:", *notes])

        key = self.session_key(update)
        s = self.agent.session(key)
        if s.busy:
            s.pending.append(full)
            await msg.reply_text("📝 Đã nhận, mình sẽ áp dụng vào việc đang làm.")
            return
        s.busy = True
        self.app.create_task(self.run_turn(chat_id, key, full, images))

    async def run_turn(self, chat_id: int, key: str, text: str, images: list[Path]) -> None:
        typing = asyncio.create_task(self.keep_typing(chat_id))
        s = self.agent.session(key)
        try:
            answer = await self.agent.handle(key, text, images, self.make_ctx(chat_id))
            await self.send_text(chat_id, answer)
        except Exception as e:
            log.exception("Lỗi khi xử lý")
            await self.send_text(chat_id, f"⚠️ Lỗi: {type(e).__name__}: {str(e)[:500]}")
        finally:
            s.busy = False
            typing.cancel()
            leftover, s.pending = s.pending, []
            if leftover:  # tin nhắn đến sau khi AI vừa trả lời xong
                s.busy = True
                self.app.create_task(self.run_turn(chat_id, key, "\n".join(leftover), []))

    async def keep_typing(self, chat_id: int) -> None:
        try:
            while True:
                await self.app.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(4.5)
        except (asyncio.CancelledError, TelegramError):
            pass
