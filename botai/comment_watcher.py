"""Theo dõi bình luận mới trên các fanpage và giao cho trợ lý mạng xã hội xử lý (trả lời / báo cáo)."""

from __future__ import annotations

import asyncio
import json
import logging

from .tools import ToolError
from .tools.facebook import Graph, fetch_recent_comments, load_pages

log = logging.getLogger(__name__)


class CommentWatcher:
    def __init__(self, bot, settings):
        """bot: TelegramAgentBot của trợ lý mạng xã hội (dùng để chạy AI và báo cáo cho admin)."""
        self.bot = bot
        self.settings = settings
        self.seen_path = settings.state_dir / "seen_comments.json"
        self.seen: set[str] = set(self._load())
        self.first_run = not self.seen_path.exists()

    def _load(self) -> list[str]:
        try:
            return json.loads(self.seen_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        self.seen_path.write_text(json.dumps(sorted(self.seen)[-5000:]), encoding="utf-8")

    async def run_forever(self) -> None:
        interval = self.settings.fb_watch_interval
        log.info("Theo dõi bình luận Facebook mỗi %ss (tự trả lời: %s)", interval, self.settings.fb_auto_reply)
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("Lỗi khi kiểm tra bình luận")
            await asyncio.sleep(interval)

    async def poll_once(self) -> None:
        graph = Graph(self.settings.fb_graph_version)
        new = []
        for page in load_pages(self.settings.secrets_dir):
            try:
                comments = await fetch_recent_comments(graph, page)
            except ToolError as e:
                log.warning("Page %s: %s", page.name, e)
                continue
            for c in comments:
                if c["comment_id"] in self.seen:
                    continue
                self.seen.add(c["comment_id"])
                if c["from_id"] == page.page_id:  # bình luận của chính page
                    continue
                new.append((page, c))
        if self.first_run:
            # Lần đầu: chỉ đánh dấu đã thấy, không trả lời bình luận cũ.
            self.first_run = False
            self._save()
            return
        self._save()
        for page, c in new:
            await self.handle(page, c)

    async def handle(self, page, c: dict) -> None:
        chat_id = self.settings.admin_chat_id
        if chat_id is None:
            log.warning("Chưa đặt ADMIN_CHAT_ID nên không báo cáo được bình luận")
            return
        mode = (
            "Hãy trả lời bình luận này bằng fb_reply_comment nếu tài liệu FAQ/bài viết đủ thông tin "
            "(lịch sự, ngắn, mời inbox/để lại SĐT khi hỏi giá chi tiết). Nếu không chắc, KHÔNG trả lời mà hỏi chủ."
            if self.settings.fb_auto_reply
            else "KHÔNG tự trả lời. Hãy đề xuất 1 câu trả lời mẫu để chủ duyệt."
        )
        prompt = (
            f"[Bình luận mới trên fanpage '{page.name}']\n"
            f"Người bình luận: {c['from_name']}\nNội dung: {c['message']}\n"
            f"comment_id: {c['comment_id']}\nBài viết: {c['post_message']}\nLink: {c['post_link']}\n\n"
            f"{mode}\nSau đó báo cáo ngắn cho chủ: ai hỏi gì, bạn đã làm gì."
        )
        key = f"fb-watch:{chat_id}"
        try:
            answer = await self.bot.agent.handle(key, prompt, [], self.bot.make_ctx(chat_id))
            await self.bot.send_text(chat_id, "💬 " + answer)
        except Exception as e:
            log.exception("Lỗi xử lý bình luận")
            await self.bot.send_text(chat_id, f"⚠️ Lỗi xử lý bình luận {c['comment_id']}: {e}")
