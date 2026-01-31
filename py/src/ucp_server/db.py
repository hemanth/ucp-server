"""SQLite database for persistence."""

import sqlite3
from pathlib import Path
from typing import Optional

from ucp_server.config import config
from ucp_server.logger import logger


# Ensure data directory exists
data_dir = Path(config.DATABASE_PATH).parent
data_dir.mkdir(parents=True, exist_ok=True)

# Connect to database
_connection: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    """Get database connection."""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _init_schema(_connection)
        logger.info("database_initialized", path=config.DATABASE_PATH)
    return _connection


def _init_schema(conn: sqlite3.Connection):
    """Initialize database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS checkouts (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            currency TEXT NOT NULL,
            buyer_json TEXT,
            fulfillment_json TEXT,
            payment_json TEXT,
            totals_json TEXT NOT NULL,
            links_json TEXT NOT NULL,
            messages_json TEXT,
            continue_url TEXT,
            expires_at TEXT,
            order_json TEXT,
            payment_intent_id TEXT,
            paypal_order_id TEXT,
            payment_provider TEXT,
            payment_status TEXT DEFAULT 'none',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS line_items (
            id TEXT NOT NULL,
            checkout_id TEXT NOT NULL,
            item_json TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            totals_json TEXT,
            PRIMARY KEY (id, checkout_id),
            FOREIGN KEY (checkout_id) REFERENCES checkouts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            checkout_id TEXT NOT NULL,
            permalink_url TEXT,
            buyer_json TEXT,
            line_items_json TEXT NOT NULL,
            totals_json TEXT NOT NULL,
            fulfillment_json TEXT,
            payment_intent_id TEXT,
            paypal_order_id TEXT,
            payment_provider TEXT,
            payment_status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (checkout_id) REFERENCES checkouts(id)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            item_id TEXT PRIMARY KEY,
            stock INTEGER NOT NULL DEFAULT 0,
            reserved INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_line_items_checkout ON line_items(checkout_id);
        CREATE INDEX IF NOT EXISTS idx_orders_checkout ON orders(checkout_id);
        CREATE INDEX IF NOT EXISTS idx_checkouts_status ON checkouts(status);
    """)
    conn.commit()


def is_db_healthy() -> bool:
    """Check database health."""
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False
