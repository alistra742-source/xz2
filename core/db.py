"""Tiny DB layer that speaks Postgres (DATABASE_URL) or SQLite (dev fallback).

Queries are written with '?' placeholders and translated for Postgres.
"""
import os
import re
import threading
import time
from contextlib import contextmanager

from . import config

IS_PG = bool(config.DATABASE_URL)

_local = threading.local()
_pool_lock = threading.Lock()
_pg_pool = None


def _init_pg():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pool_lock:
        if _pg_pool is None:
            import psycopg2.pool
            dsn = config.DATABASE_URL
            if dsn.startswith("postgres://"):
                dsn = "postgresql://" + dsn[len("postgres://"):]
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn)
    return _pg_pool


def _sqlite_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        import sqlite3
        os.makedirs(os.path.dirname(config.SQLITE_PATH), exist_ok=True)
        conn = sqlite3.connect(config.SQLITE_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        _local.conn = conn
    return conn


_PLACEHOLDER = re.compile(r"\?")


def _q(sql):
    if IS_PG:
        return _PLACEHOLDER.sub("%s", sql)
    return sql


@contextmanager
def cursor(commit=True):
    """Yield a DB cursor. Rolls back on exception."""
    if IS_PG:
        pool = _init_pg()
        conn = pool.getconn()
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            pool.putconn(conn)
    else:
        conn = _sqlite_conn()
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def execute(sql, params=()):
    with cursor() as cur:
        cur.execute(_q(sql), params)


def query(sql, params=()):
    with cursor(commit=False) as cur:
        cur.execute(_q(sql), params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def insert_returning_id(sql, params=()):
    """INSERT ... (no RETURNING in the sql) -> new row id."""
    if IS_PG:
        with cursor() as cur:
            cur.execute(_q(sql + " RETURNING id"), params)
            row = cur.fetchone()
            return row["id"] if row else None
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid


SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS accounts (
    id            SERIAL PRIMARY KEY,
    secret_hash   TEXT UNIQUE NOT NULL,
    username      TEXT NOT NULL,
    coins         INTEGER NOT NULL DEFAULT 0,
    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    verified      BOOLEAN NOT NULL DEFAULT FALSE,
    demo_used     BOOLEAN NOT NULL DEFAULT FALSE,
    ads_watched   INTEGER NOT NULL DEFAULT 0,
    coins_spent   INTEGER NOT NULL DEFAULT 0,
    created_at    DOUBLE PRECISION NOT NULL,
    last_seen     DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token          TEXT PRIMARY KEY,
    account_id     INTEGER NOT NULL,
    created_at     DOUBLE PRECISION NOT NULL,
    last_captcha   DOUBLE PRECISION NOT NULL DEFAULT 0,
    pending_solves INTEGER NOT NULL DEFAULT 2,
    ip             TEXT,
    ua             TEXT
);
CREATE TABLE IF NOT EXISTS ad_sessions (
    id          SERIAL PRIMARY KEY,
    account_id  INTEGER NOT NULL,
    pack        TEXT NOT NULL,
    required    INTEGER NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    coins       INTEGER NOT NULL,
    state       TEXT NOT NULL DEFAULT 'open',
    suspicion   INTEGER NOT NULL DEFAULT 0,
    created_at  DOUBLE PRECISION NOT NULL,
    slot_nonce  TEXT,
    slot_start  DOUBLE PRECISION DEFAULT 0,
    slot_beats  INTEGER NOT NULL DEFAULT 0,
    slot_last   DOUBLE PRECISION DEFAULT 0
);
CREATE TABLE IF NOT EXISTS captchas (
    id          TEXT PRIMARY KEY,
    session_tok TEXT,
    purpose     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL,
    solved      BOOLEAN NOT NULL DEFAULT FALSE,
    attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS promo_codes (
    code       TEXT PRIMARY KEY,
    coins      INTEGER NOT NULL,
    uses_left  INTEGER NOT NULL DEFAULT 1,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses (
    code       TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    used_at    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (code, account_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id         SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL,
    platform   TEXT NOT NULL,
    service    TEXT NOT NULL,
    link       TEXT NOT NULL,
    link_key   TEXT NOT NULL,
    cost       INTEGER NOT NULL,
    amount     INTEGER NOT NULL,
    baseline   INTEGER NOT NULL DEFAULT 0,
    target     INTEGER NOT NULL DEFAULT 0,
    current    INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'queued',
    message    TEXT DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS link_locks (
    link_key     TEXT PRIMARY KEY,
    locked_until DOUBLE PRECISION NOT NULL,
    order_id     INTEGER
);
CREATE TABLE IF NOT EXISTS stats (
    k TEXT PRIMARY KEY,
    v DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS captcha_cache (
    img_hash TEXT PRIMARY KEY,
    answer   TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_sessions_acct ON sessions(account_id);
"""

SCHEMA_SQLITE = (
    SCHEMA_PG.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace("DOUBLE PRECISION", "REAL")
    .replace("BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0")
    .replace("BOOLEAN NOT NULL DEFAULT TRUE", "INTEGER NOT NULL DEFAULT 1")
)


def init_db():
    schema = SCHEMA_PG if IS_PG else SCHEMA_SQLITE
    for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
        try:
            execute(stmt)
        except Exception as e:  # pragma: no cover
            print(f"[DB] schema stmt failed: {e}", flush=True)
    for k in ("accounts_created", "coins_spent", "coins_earned", "ads_watched", "orders_done"):
        try:
            execute("INSERT INTO stats (k, v) VALUES (?, 0)", (k,))
        except Exception:
            pass
    print(f"[DB] ready ({'postgres' if IS_PG else 'sqlite:' + config.SQLITE_PATH})", flush=True)


def bump_stat(key, amount=1):
    try:
        execute("UPDATE stats SET v = v + ? WHERE k = ?", (amount, key))
    except Exception:
        pass


def get_stats():
    out = {}
    for row in query("SELECT k, v FROM stats"):
        out[row["k"]] = int(row["v"])
    return out


def now():
    return time.time()
