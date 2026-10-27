"""Kỹ năng Facebook Fanpage qua Graph API chính thức.

Danh sách page lưu ở data/secrets/facebook_pages.txt trên MÁY CỦA BẠN (không gửi lên web nào khác ngoài
Facebook). AI chỉ thấy TÊN page, không bao giờ thấy access token.

Định dạng mỗi dòng (dấu | hoặc tab hoặc dấu phẩy):
    ten_page | page_id | page_access_token
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import ToolContext, ToolError, tool

PAGES_FILE = "facebook_pages.txt"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class Page:
    name: str
    page_id: str
    token: str


def parse_pages(text: str) -> list[Page]:
    pages = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[|\t,;]", line) if p.strip()]
        if len(parts) == 2:
            parts = [parts[0], parts[0], parts[1]]
        if len(parts) < 3 or not parts[1].isdigit():
            raise ValueError(f"Dòng không hợp lệ (cần: tên | page_id | token): {line[:40]}...")
        pages.append(Page(name=parts[0], page_id=parts[1], token=parts[2]))
    return pages


def load_pages(secrets_dir: Path) -> list[Page]:
    path = secrets_dir / PAGES_FILE
    if not path.exists():
        return []
    return parse_pages(path.read_text(encoding="utf-8"))


def find_page(secrets_dir: Path, key: str) -> Page:
    pages = load_pages(secrets_dir)
    if not pages:
        raise ToolError("Chưa có page nào. Hãy gửi file .txt (tên | page_id | token) và yêu cầu nhập danh sách page.")
    key_l = key.strip().lower()
    for p in pages:
        if key_l in (p.page_id, p.name.lower()):
            return p
    names = ", ".join(p.name for p in pages)
    raise ToolError(f"Không thấy page '{key}'. Các page có: {names}")


class Graph:
    def __init__(self, version: str):
        self.base = f"https://graph.facebook.com/{version}"
        self.video_base = f"https://graph-video.facebook.com/{version}"

    async def request(self, method: str, url: str, **kw) -> dict:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20)) as client:
            r = await client.request(method, url, **kw)
        try:
            data = r.json()
        except ValueError:
            raise ToolError(f"Facebook trả về lỗi HTTP {r.status_code}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise ToolError(f"Facebook lỗi {err.get('code')}: {err.get('message')}")
        return data


@tool(
    "fb_import_pages",
    "Nhập file danh sách page (người dùng vừa gửi .txt dạng 'tên | page_id | token') vào kho bí mật. "
    "File gốc sẽ bị xóa khỏi thư mục làm việc. Không bao giờ đọc nội dung file này.",
    {"path": {"type": "string"}},
    ["path"],
)
async def fb_import_pages(ctx: ToolContext, path: str) -> str:
    src = ctx.resolve(path)
    try:
        pages = parse_pages(src.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ToolError(str(e))
    dest = ctx.settings.secrets_dir / PAGES_FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    dest.chmod(0o600)
    src.unlink()
    return f"Đã nhập {len(pages)} page: " + ", ".join(p.name for p in pages)


@tool("fb_list_pages", "Liệt kê các fanpage đã cấu hình (chỉ tên và ID).", {})
async def fb_list_pages(ctx: ToolContext) -> str:
    pages = load_pages(ctx.settings.secrets_dir)
    return "\n".join(f"- {p.name} (id {p.page_id})" for p in pages) or "Chưa có page nào."


@tool(
    "fb_post",
    "Đăng bài lên fanpage. Có media_path (video/ảnh trong thư mục làm việc) thì đăng video/ảnh kèm nội dung; "
    "không có thì đăng bài chữ (có thể kèm link). Trả về link bài viết.",
    {
        "page": {"type": "string", "description": "Tên hoặc ID page"},
        "message": {"type": "string", "description": "Nội dung bài viết / caption"},
        "media_path": {"type": "string"},
        "link": {"type": "string"},
        "title": {"type": "string", "description": "Tiêu đề video (tùy chọn)"},
    },
    ["page", "message"],
)
async def fb_post(ctx: ToolContext, page: str, message: str, media_path: str | None = None, link: str | None = None, title: str | None = None) -> str:
    p = find_page(ctx.settings.secrets_dir, page)
    g = Graph(ctx.settings.fb_graph_version)
    if media_path:
        f = ctx.resolve(media_path)
        ext = f.suffix.lower()
        with f.open("rb") as fh:
            if ext in VIDEO_EXT:
                data = {"description": message, "access_token": p.token}
                if title:
                    data["title"] = title
                res = await g.request("POST", f"{g.video_base}/{p.page_id}/videos", data=data, files={"source": (f.name, fh)})
                return f"Đã đăng video lên {p.name}: https://www.facebook.com/{p.page_id}/videos/{res['id']}"
            if ext in PHOTO_EXT:
                res = await g.request("POST", f"{g.base}/{p.page_id}/photos", data={"caption": message, "access_token": p.token}, files={"source": (f.name, fh)})
                post_id = res.get("post_id") or res["id"]
                return f"Đã đăng ảnh lên {p.name}: https://www.facebook.com/{post_id}"
        raise ToolError(f"Định dạng {ext} chưa hỗ trợ")
    data = {"message": message, "access_token": p.token}
    if link:
        data["link"] = link
    res = await g.request("POST", f"{g.base}/{p.page_id}/feed", data=data)
    return f"Đã đăng bài lên {p.name}: https://www.facebook.com/{res['id']}"


async def fetch_recent_comments(graph: Graph, page: Page, posts: int = 10) -> list[dict]:
    fields = "id,message,permalink_url,comments.limit(50).order(reverse_chronological){id,message,from,created_time}"
    res = await graph.request("GET", f"{graph.base}/{page.page_id}/feed", params={"fields": fields, "limit": posts, "access_token": page.token})
    out = []
    for post in res.get("data", []):
        for c in (post.get("comments") or {}).get("data", []):
            out.append(
                {
                    "comment_id": c["id"],
                    "from_id": (c.get("from") or {}).get("id"),
                    "from_name": (c.get("from") or {}).get("name", "(ẩn danh)"),
                    "message": c.get("message", ""),
                    "created_time": c.get("created_time"),
                    "post_id": post["id"],
                    "post_message": (post.get("message") or "")[:300],
                    "post_link": post.get("permalink_url"),
                }
            )
    return out


@tool(
    "fb_list_comments",
    "Xem bình luận mới nhất trên các bài gần đây của một page.",
    {"page": {"type": "string"}, "posts": {"type": "integer", "description": "Số bài gần nhất, mặc định 5"}},
    ["page"],
)
async def fb_list_comments(ctx: ToolContext, page: str, posts: int = 5) -> str:
    p = find_page(ctx.settings.secrets_dir, page)
    comments = await fetch_recent_comments(Graph(ctx.settings.fb_graph_version), p, posts)
    if not comments:
        return "Không có bình luận."
    return "\n".join(f"[{c['comment_id']}] {c['from_name']}: {c['message']} (bài: {c['post_message'][:60]})" for c in comments)


@tool(
    "fb_reply_comment",
    "Trả lời một bình luận trên fanpage (dưới danh nghĩa page).",
    {"page": {"type": "string"}, "comment_id": {"type": "string"}, "message": {"type": "string"}},
    ["page", "comment_id", "message"],
)
async def fb_reply_comment(ctx: ToolContext, page: str, comment_id: str, message: str) -> str:
    p = find_page(ctx.settings.secrets_dir, page)
    g = Graph(ctx.settings.fb_graph_version)
    res = await g.request("POST", f"{g.base}/{comment_id}/comments", data={"message": message, "access_token": p.token})
    return f"Đã trả lời bình luận {comment_id} (id phản hồi {res.get('id')})"


TOOLS = [fb_import_pages, fb_list_pages, fb_post, fb_list_comments, fb_reply_comment]
