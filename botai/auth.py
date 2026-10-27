"""Kiểm soát truy cập bằng mã kết nối (pairing code).

Người lạ nhắn bot -> bot trả về mã 6 ký tự. Chủ bot duyệt mã bằng lệnh
`python -m botai approve <MÃ>` trên máy chủ hoặc `/approve <MÃ>` trong Telegram
(nếu là admin). Sau đó người đó dùng được tất cả trợ lý.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bỏ ký tự dễ nhầm (0/O, 1/I)
CODE_TTL_SECONDS = 3600


class AccessControl:
    def __init__(self, state_dir: Path, admin_ids: set[int] | None = None):
        self.state_dir = state_dir
        self.admin_ids = set(admin_ids or ())
        self.allowed_path = state_dir / "allowed_users.json"
        self.pending_path = state_dir / "pending_codes.json"

    # -- lưu trữ ---------------------------------------------------------
    def _read(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _write(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def allowed_users(self) -> dict[str, dict]:
        return self._read(self.allowed_path, {})

    def _pending(self) -> dict[str, dict]:
        now = time.time()
        return {c: p for c, p in self._read(self.pending_path, {}).items() if now - p["ts"] < CODE_TTL_SECONDS}

    # -- API -------------------------------------------------------------
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def is_allowed(self, user_id: int) -> bool:
        return self.is_admin(user_id) or str(user_id) in self.allowed_users()

    def request_code(self, user_id: int, name: str) -> str:
        pending = self._pending()
        for code, p in pending.items():
            if p["user_id"] == user_id:
                return code
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        pending[code] = {"user_id": user_id, "name": name, "ts": time.time()}
        self._write(self.pending_path, pending)
        return code

    def approve(self, code: str) -> dict | None:
        code = code.strip().upper()
        pending = self._pending()
        entry = pending.pop(code, None)
        if not entry:
            return None
        users = self.allowed_users()
        users[str(entry["user_id"])] = {"name": entry["name"], "approved_at": int(time.time())}
        self._write(self.allowed_path, users)
        self._write(self.pending_path, pending)
        return entry

    def revoke(self, user_id: int) -> bool:
        users = self.allowed_users()
        if users.pop(str(user_id), None) is None:
            return False
        self._write(self.allowed_path, users)
        return True
