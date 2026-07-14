"""
Gestion de la base de données SQLite pour l'historique des prix.
Aucune dépendance externe : sqlite3 est inclus dans Python.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "prices.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_name TEXT NOT NULL,
            set_code TEXT,
            source TEXT NOT NULL,
            price_eur REAL,
            currency TEXT DEFAULT 'EUR',
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_card_source_time
        ON price_history (card_name, source, fetched_at)
    """)
    conn.commit()
    conn.close()


def insert_price(card_name: str, set_code: str | None, source: str, price_eur: float | None):
    if price_eur is None:
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO price_history (card_name, set_code, source, price_eur, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (card_name, set_code, source, price_eur, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_price_history(card_name: str, source: str, limit: int = 50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM price_history WHERE card_name = ? AND source = ? ORDER BY fetched_at DESC LIMIT ?",
        (card_name, source, limit),
    ).fetchall()
    conn.close()
    return rows


def get_latest_price(card_name: str, source: str):
    rows = get_price_history(card_name, source, limit=1)
    return rows[0] if rows else None


def get_all_tracked_cards():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT card_name FROM price_history").fetchall()
    conn.close()
    return [r["card_name"] for r in rows]
