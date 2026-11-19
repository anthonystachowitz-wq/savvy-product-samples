"""Small daily budget for live model calls on a public demo."""
from __future__ import annotations

import sqlite3
from datetime import date

from . import config

DB_PATH = config.REPO_ROOT / "app.db"


class BudgetExceeded(RuntimeError):
    """Raised when the demo has used its daily allowance of live calls."""


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS suite_llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            day TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return connection


def used_today() -> int:
    try:
        connection = _connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM suite_llm_calls WHERE day = ?", (date.today().isoformat(),)
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()
    except sqlite3.Error:
        return 0


def check_and_increment(module: str) -> None:
    limit = config.daily_llm_limit()
    if limit <= 0:
        raise BudgetExceeded("Live model calls are disabled for this deployment.")
    connection = _connect()
    try:
        today = date.today().isoformat()
        rows = connection.execute(
            "SELECT COUNT(*) FROM suite_llm_calls WHERE day = ?", (today,)
        ).fetchone()
        if rows and int(rows[0]) >= limit:
            raise BudgetExceeded("The daily live-call budget for this demo is used up. Try again tomorrow.")
        connection.execute(
            "INSERT INTO suite_llm_calls (module, day, created_at) VALUES (?, ?, datetime('now'))",
            (module, today),
        )
        connection.commit()
    finally:
        connection.close()
