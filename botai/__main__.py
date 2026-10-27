"""Điểm chạy chính.

    python -m botai                 # chạy toàn bộ đội trợ lý
    python -m botai approve MÃ      # duyệt mã kết nối
    python -m botai users           # xem người dùng được phép
    python -m botai revoke USER_ID  # thu hồi quyền
    python -m botai check           # kiểm tra cấu hình, không chạy bot
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import signal
import sys
from pathlib import Path

from .auth import AccessControl
from .config import ROOT, Settings, load_agents, load_dotenv


def _setup() -> tuple[Settings, Path]:
    load_dotenv(ROOT / ".env")
    settings = Settings.from_env()
    settings.ensure_dirs()
    cfg_path = Path(os.environ.get("BOTAI_AGENTS", ROOT / "config" / "agents.yaml"))
    if not cfg_path.exists():
        sys.exit(f"Không thấy {cfg_path}. Hãy chép config/agents.example.yaml thành config/agents.yaml")
    return settings, cfg_path


def cmd_check(settings: Settings, cfg_path: Path) -> int:
    agents = load_agents(cfg_path)
    ok = True
    print(f"Thư mục dữ liệu: {settings.data_dir}")
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        print(f"{'✔' if found else '✘'} {tool}: {found or 'CHƯA CÀI'}")
        ok &= bool(found)
    keys = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
    for a in agents:
        tok = "✔" if a.telegram_token else "– chưa có (" + a.telegram_token_env + ", chỉ chạy qua delegate)"
        key = keys.get(a.provider)
        has_key = bool(os.environ.get(key or ""))
        print(f"- {a.name}: {a.provider}:{a.model} | token Telegram {tok} | {key} {'✔' if has_key else '✘'} | kỹ năng {a.skills}")
        ok &= has_key
    if not any(a.telegram_token for a in agents):
        print("✘ Chưa có bot nào có token Telegram")
        ok = False
    print(f"Admin IDs: {sorted(settings.admin_ids) or '(chưa đặt)'}")
    print("OK" if ok else "Còn thiếu cấu hình ở trên (✘)")
    return 0 if ok else 1


async def run(settings: Settings, cfg_path: Path) -> None:
    from .agent import Team
    from .comment_watcher import CommentWatcher
    from .telegram_bot import TelegramAgentBot

    configs = load_agents(cfg_path)
    team = Team(configs, settings)
    access = AccessControl(settings.state_dir, settings.admin_ids)
    bots: dict[str, TelegramAgentBot] = {}
    for cfg in configs:
        if not cfg.telegram_token:
            logging.warning("Bỏ qua bot %s: chưa có %s trong .env (vẫn dùng được qua delegate)", cfg.name, cfg.telegram_token_env)
            continue
        bots[cfg.name] = TelegramAgentBot(team.agents[cfg.name], team, access)
    if not bots:
        sys.exit("Không có bot nào có token Telegram. Kiểm tra file .env")
    for b in bots.values():
        await b.start()

    tasks = []
    if settings.fb_watch_interval > 0:
        name = settings.fb_watch_agent or next(iter(bots))
        if name not in bots:
            sys.exit(f"FB_WATCH_AGENT={name} không có bot Telegram đang chạy")
        tasks.append(asyncio.create_task(CommentWatcher(bots[name], settings).run_forever()))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    logging.info("Đội trợ lý đã sẵn sàng: %s. Nhấn Ctrl+C để dừng.", ", ".join(bots))
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        for b in bots.values():
            await b.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="botai", description="Đội trợ lý AI trên Telegram")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("check")
    sub.add_parser("users")
    p = sub.add_parser("approve")
    p.add_argument("code")
    p = sub.add_parser("revoke")
    p.add_argument("user_id", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings, cfg_path = _setup()
    access = AccessControl(settings.state_dir, settings.admin_ids)

    if args.cmd == "approve":
        entry = access.approve(args.code)
        print(f"Đã duyệt {entry['name']} (id {entry['user_id']})" if entry else "Mã không đúng hoặc đã hết hạn (1 giờ)")
    elif args.cmd == "users":
        for uid, info in access.allowed_users().items():
            print(uid, info.get("name"))
    elif args.cmd == "revoke":
        print("Đã thu hồi" if access.revoke(args.user_id) else "Không có người dùng này")
    elif args.cmd == "check":
        sys.exit(cmd_check(settings, cfg_path))
    else:
        try:
            asyncio.run(run(settings, cfg_path))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
