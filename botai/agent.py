"""Trợ lý AI: ghép vai trò + bộ nhớ + kỹ năng, giữ lịch sử từng cuộc trò chuyện, giao việc cho nhau."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import AgentConfig, Settings
from .llm import Usage, make_llm
from .tools import Tool, ToolContext, ToolError, ToolResult, get_skill_tools, tool

log = logging.getLogger(__name__)

BASE_RULES = """Bạn là một trợ lý AI làm việc 24/7 cho chủ doanh nghiệp, được điều khiển qua Telegram.
Quy tắc làm việc:
- Trả lời bằng tiếng Việt, ngắn gọn, rõ ràng (tin nhắn Telegram, không dùng bảng Markdown).
- Việc nhiều bước: nói ngắn kế hoạch + ước tính chi phí/thời gian trước, rồi làm. Thiếu thông tin quan trọng
  (ví dụ tọa độ lô đất, page cần đăng) thì hỏi lại trước khi làm.
- Khi công cụ báo lỗi: đọc kỹ lỗi, tự sửa (đổi tham số, cách làm khác) rồi thử lại. Sửa được lỗi mới thì dùng
  save_lesson để ghi lại cách xử lý, lần sau không mắc lại.
- File người dùng gửi nằm trong thư mục inbox/. Làm xong thì gửi kết quả bằng send_file.
- Không bao giờ tiết lộ token, mật khẩu, khóa API.
"""


@dataclass
class Session:
    history: list = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    busy: bool = False
    usage: Usage = field(default_factory=Usage)
    started: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Agent:
    def __init__(self, cfg: AgentConfig, settings: Settings, team: "Team"):
        self.cfg = cfg
        self.settings = settings
        self.team = team
        self.llm = make_llm(cfg)
        self.sessions: dict[str, Session] = {}
        tools: list[Tool] = []
        for skill in cfg.skills:
            tools.extend(get_skill_tools(skill))
        if cfg.allow_shell and "shell" not in cfg.skills:
            tools.extend(get_skill_tools("shell"))
        if cfg.delegate_to:
            tools.append(self._delegate_tool())
        self.tools = {t.name: t for t in tools}

    # -- prompt ----------------------------------------------------------
    def system_prompt(self) -> str:
        parts = [BASE_RULES, f"Tên bạn: {self.cfg.display_name}.\nVai trò của bạn:\n{self.cfg.role.strip()}"]
        if self.cfg.delegate_to:
            team = "\n".join(f"- {n}: {self.team.agents[n].cfg.display_name}" for n in self.cfg.delegate_to)
            parts.append("Bạn là điều phối viên. Có thể giao việc chuyên môn cho các trợ lý sau bằng tool delegate:\n" + team)
        for name in self.cfg.knowledge_files:
            p = self.settings.knowledge_dir / name
            if p.exists():
                parts.append(f"Tài liệu tham khảo ({name}):\n{p.read_text(encoding='utf-8')[:30_000]}")
        mem = self.settings.memory_dir / f"{self.cfg.name}.md"
        if mem.exists():
            parts.append("Bài học đã ghi nhớ từ những lần trước (hãy áp dụng):\n" + mem.read_text(encoding="utf-8")[-20_000:])
        return "\n\n".join(parts)

    # -- phiên làm việc ---------------------------------------------------
    def session(self, key: str) -> Session:
        return self.sessions.setdefault(key, Session())

    def reset(self, key: str) -> None:
        self.sessions.pop(key, None)

    async def execute(self, ctx: ToolContext, name: str, args: dict) -> ToolResult:
        t = self.tools.get(name)
        if not t:
            return ToolResult(f"Không có công cụ '{name}'", is_error=True)
        allowed = set(inspect.signature(t.func).parameters) - {"ctx"}
        unknown = set(args) - allowed
        if unknown:
            return ToolResult(f"Tham số không hợp lệ cho {name}: {sorted(unknown)}", is_error=True)
        log.info("[%s] tool %s %s", self.cfg.name, name, str(args)[:300])
        try:
            res = await t.func(ctx, **args)
            return res if isinstance(res, ToolResult) else ToolResult(str(res))
        except ToolError as e:
            return ToolResult(str(e), is_error=True)
        except TypeError as e:
            return ToolResult(f"Sai tham số khi gọi {name}: {e}", is_error=True)
        except Exception as e:  # lỗi bất ngờ: trả cho AI để tự xử lý
            log.exception("tool %s failed", name)
            return ToolResult(f"Lỗi khi chạy {name}: {type(e).__name__}: {e}", is_error=True)

    async def handle(self, key: str, text: str, images: list[Path], ctx: ToolContext) -> str:
        """Chạy một lượt: thêm tin nhắn người dùng, để AI làm việc với tool, trả về câu trả lời cuối."""
        s = self.session(key)
        async with s.lock:  # mỗi phiên chỉ chạy một lượt tại một thời điểm
            return await self._handle_locked(s, text, images, ctx)

    async def _handle_locked(self, s: Session, text: str, images: list[Path], ctx: ToolContext) -> str:
        note = ""
        if len(s.history) > self.cfg.max_history_messages:
            s.history = []
            note = "(Cuộc trò chuyện quá dài nên đã bắt đầu phiên mới; bài học đã lưu vẫn còn.)\n"
        s.history.append(self.llm.user_message(text, images))

        def drain() -> list[str]:
            items, s.pending = s.pending, []
            return items

        async def execute(name: str, args: dict) -> ToolResult:
            return await self.execute(ctx, name, args)

        answer = await self.llm.run(self.system_prompt(), s.history, list(self.tools.values()), execute, drain, s.usage)
        return note + (answer or "Xong.")

    # -- giao việc ---------------------------------------------------------
    def _delegate_tool(self) -> Tool:
        names = list(self.cfg.delegate_to)

        @tool(
            "delegate",
            "Giao một việc cho trợ lý chuyên môn và chờ kết quả. Mô tả việc thật đầy đủ (file nào, yêu cầu gì) "
            "vì trợ lý đó không thấy cuộc trò chuyện của bạn. Các trợ lý dùng chung thư mục làm việc.",
            {"agent": {"type": "string", "enum": names}, "task": {"type": "string"}},
            ["agent", "task"],
        )
        async def delegate(ctx: ToolContext, agent: str, task: str) -> str:
            if agent not in names:
                raise ToolError(f"Không được giao cho '{agent}'. Chọn: {', '.join(names)}")
            worker = self.team.agents[agent]
            await ctx.notify(f"➡️ Giao việc cho {worker.cfg.display_name}…")
            sub_ctx = ToolContext(
                agent=worker.cfg, settings=ctx.settings, chat_id=ctx.chat_id, workdir=ctx.workdir,
                send_file=ctx.send_file, notify=ctx.notify,
            )
            result = await worker.handle(f"delegate:{self.cfg.name}:{ctx.chat_id}", task, [], sub_ctx)
            return f"Kết quả từ {worker.cfg.display_name}:\n{result}"

        return delegate


class Team:
    def __init__(self, configs: list[AgentConfig], settings: Settings):
        self.settings = settings
        self.agents: dict[str, Agent] = {}
        for cfg in configs:
            self.agents[cfg.name] = Agent(cfg, settings, self)

    def workdir(self, chat_id: int) -> Path:
        d = self.settings.work_dir / str(chat_id)
        (d / "inbox").mkdir(parents=True, exist_ok=True)
        return d
