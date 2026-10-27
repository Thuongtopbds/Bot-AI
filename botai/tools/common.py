"""Kỹ năng chung: xem file, gửi file, ghi nhớ bài học, (tùy chọn) chạy lệnh shell."""

from __future__ import annotations

import time

from . import ToolContext, ToolError, run_cmd, tool


@tool(
    "list_files",
    "Liệt kê file trong thư mục làm việc của cuộc trò chuyện (file người dùng gửi nằm trong inbox/).",
    {"subdir": {"type": "string", "description": "Thư mục con, mặc định là gốc"}},
)
async def list_files(ctx: ToolContext, subdir: str = ".") -> str:
    base = ctx.resolve(subdir)
    lines = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            lines.append(f"{ctx.rel(p)}  ({p.stat().st_size / 1_048_576:.2f} MB)")
        if len(lines) >= 200:
            lines.append("... (còn nữa)")
            break
    return "\n".join(lines) or "(thư mục trống)"


@tool(
    "write_text_file",
    "Ghi nội dung văn bản ra file trong thư mục làm việc (kịch bản, phụ đề .srt, caption...).",
    {"path": {"type": "string"}, "content": {"type": "string"}},
    ["path", "content"],
)
async def write_text_file(ctx: ToolContext, path: str, content: str) -> str:
    p = ctx.resolve(path, must_exist=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Đã ghi {ctx.rel(p)} ({len(content)} ký tự)"


@tool(
    "read_text_file",
    "Đọc file văn bản trong thư mục làm việc (tối đa 50.000 ký tự).",
    {"path": {"type": "string"}},
    ["path"],
)
async def read_text_file(ctx: ToolContext, path: str) -> str:
    p = ctx.resolve(path)
    if p.stat().st_size > 2_000_000:
        raise ToolError("File quá lớn để đọc dạng văn bản")
    return p.read_text(encoding="utf-8", errors="replace")[:50_000]


@tool(
    "send_file",
    "Gửi một file (video, ảnh, tài liệu) từ thư mục làm việc về Telegram cho người dùng.",
    {"path": {"type": "string"}, "caption": {"type": "string"}},
    ["path"],
)
async def send_file(ctx: ToolContext, path: str, caption: str = "") -> str:
    p = ctx.resolve(path)
    await ctx.send_file(p, caption)
    return f"Đã gửi {ctx.rel(p)}"


@tool(
    "send_progress",
    "Gửi ngay một tin nhắn ngắn báo tiến độ cho người dùng (dùng khi việc kéo dài).",
    {"text": {"type": "string"}},
    ["text"],
)
async def send_progress(ctx: ToolContext, text: str) -> str:
    await ctx.notify(text)
    return "Đã báo tiến độ"


@tool(
    "save_lesson",
    "Ghi lại một bài học/kinh nghiệm vào bộ nhớ lâu dài để lần sau không lặp lại lỗi "
    "(ví dụ: cách sửa một lỗi thư viện, sở thích của người dùng). Viết ngắn gọn, cụ thể.",
    {"lesson": {"type": "string"}},
    ["lesson"],
)
async def save_lesson(ctx: ToolContext, lesson: str) -> str:
    path = ctx.settings.memory_dir / f"{ctx.agent.name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- [{time.strftime('%Y-%m-%d')}] {lesson.strip()}\n")
    return "Đã lưu vào bộ nhớ"


@tool(
    "run_shell",
    "Chạy một lệnh shell trong thư mục làm việc (ví dụ cài/hạ phiên bản thư viện bằng pip, "
    "kiểm tra ffmpeg). Chỉ dùng khi thật cần để tự sửa lỗi. Không đọc/ghi thư mục secrets.",
    {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "Giây, mặc định 600"},
    },
    ["command"],
)
async def run_shell(ctx: ToolContext, command: str, timeout: int = 600) -> str:
    if not ctx.agent.allow_shell:
        raise ToolError("Trợ lý này không được phép chạy shell (allow_shell: false)")
    if "secrets" in command or ".env" in command:
        raise ToolError("Không được truy cập file bí mật")
    code, out, err = await run_cmd(["bash", "-lc", command], cwd=ctx.workdir, timeout=timeout)
    return f"exit={code}\n--- stdout ---\n{out[-6000:]}\n--- stderr ---\n{err[-4000:]}"


TOOLS = [list_files, write_text_file, read_text_file, send_file, send_progress, save_lesson]
SHELL_TOOLS = [run_shell]
