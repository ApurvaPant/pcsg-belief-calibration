"""Drop and recreate every table for a clean graph.

Usage (from the backend/ directory):
    ./.venv/Scripts/python.exe scripts/reset_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a plain script: make the backend dir importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402


def reset() -> None:
    with db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in tables:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    db.init_db()
    print(f"Reset {settings.db_path}")
    print(f"Dropped {len(tables)} tables: {', '.join(tables) or '(none)'}")
    with db.get_conn() as conn:
        now = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f"Recreated {len(now)} tables: {', '.join(now)}")


if __name__ == "__main__":
    reset()
