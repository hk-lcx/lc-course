"""用户账号存储。

独立于 LangGraph checkpointer 的 SQLite 文件（`db/auth.db`），避免与会话表混在一起。
路径基于本文件位置推导，所以不受进程工作目录影响。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parents[2] / "db" / "auth.db"

_lock = threading.RLock()
_connection: Optional[sqlite3.Connection] = None


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            token_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # 兼容旧库：老表没有 token_version 时补上
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "token_version" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    global _connection
    with _lock:
        if _connection is None:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _connection = sqlite3.connect(DB_PATH, check_same_thread=False)
            _connection.row_factory = sqlite3.Row
            _connection.execute("PRAGMA journal_mode=WAL")
            _init_schema(_connection)
    return _connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_user(username: str, password_hash: str) -> Optional[int]:
    """插入新用户；账号已存在时返回 None。"""
    with _lock:
        conn = _get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, _now_iso()),
            )
            conn.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            conn.rollback()
            return None


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with _lock:
        conn = _get_connection()
        return conn.execute(
            "SELECT id, username, password_hash, created_at, token_version FROM users WHERE username = ?",
            (username,),
        ).fetchone()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with _lock:
        conn = _get_connection()
        return conn.execute(
            "SELECT id, username, password_hash, created_at, token_version FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


def bump_token_version(user_id: int) -> bool:
    """退出登录：让该账号此前签发的所有 token 失效。"""
    with _lock:
        conn = _get_connection()
        cursor = conn.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
