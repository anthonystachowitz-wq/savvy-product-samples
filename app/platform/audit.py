"""One audit log shared by every module."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config

DB_PATH = config.REPO_ROOT / "app.db"


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suite_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


def record(
    module: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    detail: str = "",
) -> None:
    connection = _connect()
    try:
        connection.execute(
            """
            INSERT INTO suite_audit (module, actor, action, entity_type, entity_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                module,
                actor,
                action,
                entity_type,
                entity_id,
                detail,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def recent(module: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        if module:
            rows = connection.execute(
                "SELECT * FROM suite_audit WHERE module = ? ORDER BY id DESC LIMIT ?",
                (module, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM suite_audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def count(module: str | None = None) -> int:
    connection = _connect()
    try:
        if module:
            row = connection.execute("SELECT COUNT(*) FROM suite_audit WHERE module = ?", (module,)).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) FROM suite_audit").fetchone()
        return int(row[0]) if row else 0
    finally:
        connection.close()
