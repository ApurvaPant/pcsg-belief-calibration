"""SQLite storage layer.

Memory belongs to the **user**, not the session. A user has many sessions
(chats); every session shares the user's single temporal node graph. Evidence
records which session (and message) an assertion came from, so the inspector
can attribute cross-session facts.

The `nodes` table is temporal: reconciliation never destructively overwrites —
a contradicted node has its `valid_to`/`status` closed and a new row inserted
with `previous_node_id` pointing back at it.
"""
from __future__ import annotations

import sqlite3
import threading as _threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from .config import settings

# The app is single-user today; this is the seeded default owner.
DEFAULT_USER_ID = "user_default"
DEFAULT_USER_LABEL = "You"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE,
    password_hash TEXT,
    label         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    title       TEXT,
    created_at  TEXT NOT NULL,
    closed_at   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    role              TEXT NOT NULL,          -- user | assistant
    content           TEXT NOT NULL,
    resolved_content  TEXT,                   -- Q-A-resolved rewrite (fix step 1)
    created_at        TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    latency_ms        INTEGER DEFAULT 0,
    salience_score    REAL,
    salient           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nodes (
    id                     TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL REFERENCES users(id),
    dimension              TEXT NOT NULL,
    key                    TEXT NOT NULL,
    value                  TEXT NOT NULL,
    confidence_raw         REAL NOT NULL,
    confidence_calibrated  REAL NOT NULL,
    valid_from             TEXT NOT NULL,
    valid_to               TEXT,
    status                 TEXT NOT NULL,      -- active | superseded | deleted
    status_detail          TEXT DEFAULT 'active', -- active|ended|achieved|abandoned
    previous_node_id       TEXT REFERENCES nodes(id),
    model                  TEXT,               -- resolved extractor model that produced it
    created_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_log (
    id                TEXT PRIMARY KEY,
    session_id        TEXT REFERENCES sessions(id),
    message_id        TEXT,
    salient           INTEGER,
    score             REAL,
    raw_score         INTEGER,    -- classifier's raw integer 0-100 (PART 9)
    reason            TEXT,
    classifier_model  TEXT,       -- resolved classifier model for this call
    frame_tokens      INTEGER DEFAULT 0,  -- PART 7: per-stage cost
    fill_tokens       INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL
);

-- Every extraction outcome, including the ones the pipeline declined to act on.
CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    session_id     TEXT REFERENCES sessions(id),
    message_id     TEXT,
    action         TEXT,          -- created|superseded|reinforced|ended|refined|ignored
    dimension      TEXT,
    key            TEXT,
    value          TEXT,
    node_id        TEXT,
    matched_label  TEXT,          -- the node a REINFORCE/UPDATE resolved to
    similarity     REAL,          -- resolver similarity for that match
    reason_code    TEXT,          -- nullable; why the pipeline declined to act
    extractor_json TEXT,          -- full raw extractor output for the turn
    created_at     TEXT NOT NULL
);

-- Qualifiers on a node (duration, proficiency, ...). Same temporal discipline
-- as nodes: a new value for (node_id, name) supersedes the old, linked back.
CREATE TABLE IF NOT EXISTS attributes (
    id                     TEXT PRIMARY KEY,
    node_id                TEXT NOT NULL REFERENCES nodes(id),
    name                   TEXT NOT NULL,     -- snake_case, from the allowed set
    value                  TEXT NOT NULL,     -- normalised text or number
    unit                   TEXT,              -- months | years | times_per_week | ...
    confidence_raw         REAL NOT NULL,     -- never modified after insert
    confidence_calibrated  REAL,
    valid_from             TEXT NOT NULL,
    valid_to               TEXT,
    status                 TEXT NOT NULL,     -- active | superseded
    previous_attribute_id  TEXT REFERENCES attributes(id),
    created_at             TEXT NOT NULL,
    updated_at             TEXT
);

-- Typed relations between two existing nodes (PART 8).
CREATE TABLE IF NOT EXISTS edges (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    source_node_id  TEXT NOT NULL REFERENCES nodes(id),
    relation        TEXT NOT NULL,   -- part_of | requires | blocks | related_to | works_on | involves | gets_along_with
    target_node_id  TEXT NOT NULL REFERENCES nodes(id),
    polarity        TEXT DEFAULT 'positive',  -- positive | negative (a negated relation)
    quote           TEXT,            -- the message span that justified the edge
    created_at      TEXT NOT NULL,
    UNIQUE (source_node_id, relation, target_node_id)
);

-- Destructive operations (END / supersede) blocked by the guard, awaiting an
-- explicit user confirmation on a later turn (never applied silently).
CREATE TABLE IF NOT EXISTS pending_confirmation (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    session_id   TEXT REFERENCES sessions(id),
    message_id   TEXT,
    kind         TEXT NOT NULL,     -- end | supersede
    node_id      TEXT,              -- the node the op would have closed
    dimension    TEXT, key TEXT, value TEXT,
    quote        TEXT,
    reason_code  TEXT,
    status       TEXT NOT NULL,     -- open | confirmed | dismissed
    created_at   TEXT NOT NULL
);

-- Every extraction attempt, for future distillation into a small extractor.
CREATE TABLE IF NOT EXISTS extraction_log (
    id             TEXT PRIMARY KEY,
    message_id     TEXT,
    context_window TEXT,        -- json: the turns passed as context
    frame_output   TEXT,        -- json: frame-call output
    fill_output    TEXT,        -- json: fill-call output
    model          TEXT,
    prompt_version TEXT,
    accepted       INTEGER,     -- 1 if at least one assertion was written
    reason_code    TEXT,        -- dominant rejection reason, if any
    status         TEXT DEFAULT 'ok',  -- ok | failed (a stage LLM call failed)
    created_at     TEXT NOT NULL
);

-- Assistant questions that opened a slot (PART 6). Two kinds (fix step 2):
--   attribute : a known node + a named attribute to fill (duration, frequency)
--   open      : a topic question; node_id may be null until the answer lands
CREATE TABLE IF NOT EXISTS pending_slots (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT REFERENCES sessions(id),
    node_id             TEXT REFERENCES nodes(id),
    attribute_name      TEXT,            -- nullable for 'open' slots
    slot_type           TEXT DEFAULT 'attribute',  -- attribute | open
    topic               TEXT,            -- the assistant's question (open slots)
    asked_at_message_id TEXT,
    status              TEXT NOT NULL,   -- open | filled | expired
    created_at          TEXT NOT NULL
);

-- Append-only per-assertion confidence signals (STEP 0). Never updated/deleted
-- (INV-02). One row per stored assertion: the raw signals the calibration study
-- consumes. self_consistency is null unless resampling was enabled for that run.
CREATE TABLE IF NOT EXISTS confidence_signals (
    id               TEXT PRIMARY KEY,
    node_id          TEXT,
    message_id       TEXT,
    dimension        TEXT,
    key              TEXT,
    value            TEXT,
    explicitness     TEXT,
    verbalized       REAL,          -- extractor confidence (0-1)
    self_consistency REAL,          -- agreement over k resamples, null if not computed
    self_consistency_reason TEXT,   -- why self_consistency is null (never a silent null)
    evidence         REAL,          -- Noisy-OR of verbalized with an explicitness prior
    intensity        REAL,          -- null unless an intensity was extracted
    created_at       TEXT NOT NULL
);

-- Evidence attaches to EXACTLY one of a node or an attribute.
CREATE TABLE IF NOT EXISTS evidence (
    id           TEXT PRIMARY KEY,
    node_id      TEXT REFERENCES nodes(id),
    attribute_id TEXT REFERENCES attributes(id),
    session_id   TEXT REFERENCES sessions(id),
    message_id   TEXT NOT NULL,
    quote        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK ((node_id IS NOT NULL) + (attribute_id IS NOT NULL) = 1)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_nodes_user     ON nodes(user_id);
CREATE INDEX IF NOT EXISTS idx_nodes_slot     ON nodes(user_id, dimension, key, status);
CREATE INDEX IF NOT EXISTS idx_messages_sess  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_node  ON evidence(node_id);
CREATE INDEX IF NOT EXISTS idx_attributes_node ON attributes(node_id, name, status);
CREATE INDEX IF NOT EXISTS idx_tokens_user    ON auth_tokens(user_id);
"""

# Tables from the pre-multi-session schema, dropped on upgrade (dev data).
_LEGACY_TABLES = ("evidence", "nodes", "messages", "conversations")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Per-thread DB override: a worker can point its whole pipeline at its OWN
# SQLite file so personas / LongMemEval questions run in parallel with no shared
# writes. Falls back to the global settings.db_path when unset for the thread.
_LOCAL = _threading.local()


def set_db_path(path) -> None:
    """Route this thread's connections to `path` (None clears the override)."""
    _LOCAL.db_path = path


def active_db_path():
    return getattr(_LOCAL, "db_path", None) or settings.db_path


def _connect() -> sqlite3.Connection:
    # timeout/busy_timeout let concurrent writers wait (not error) under the
    # overnight parallel replay; WAL already allows concurrent readers.
    conn = sqlite3.connect(active_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Transactional connection: commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent additive migrations for existing databases."""
    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "username" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "password_hash" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

    node_cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "model" not in node_cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN model TEXT")

    gate_cols = {r["name"] for r in conn.execute("PRAGMA table_info(gate_log)")}
    if gate_cols and "raw_score" not in gate_cols:
        conn.execute("ALTER TABLE gate_log ADD COLUMN raw_score INTEGER")
    if gate_cols and "frame_tokens" not in gate_cols:
        conn.execute("ALTER TABLE gate_log ADD COLUMN frame_tokens INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE gate_log ADD COLUMN fill_tokens INTEGER DEFAULT 0")

    edge_cols = {r["name"] for r in conn.execute("PRAGMA table_info(edges)")}
    if edge_cols and "quote" not in edge_cols:
        conn.execute("ALTER TABLE edges ADD COLUMN quote TEXT")
    if edge_cols and "polarity" not in edge_cols:
        conn.execute("ALTER TABLE edges ADD COLUMN polarity TEXT DEFAULT 'positive'")

    ev_cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if ev_cols2 and "similarity" not in ev_cols2:
        conn.execute("ALTER TABLE events ADD COLUMN matched_label TEXT")
        conn.execute("ALTER TABLE events ADD COLUMN similarity REAL")

    # fix step 2: pending_slots gained a type + topic for 'open' slots.
    slot_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending_slots)")}
    if slot_cols and "slot_type" not in slot_cols:
        conn.execute("ALTER TABLE pending_slots ADD COLUMN slot_type TEXT DEFAULT 'attribute'")
    if slot_cols and "topic" not in slot_cols:
        conn.execute("ALTER TABLE pending_slots ADD COLUMN topic TEXT")

    # §6: extraction_log gained a status column (ok | failed).
    xlog_cols = {r["name"] for r in conn.execute("PRAGMA table_info(extraction_log)")}
    if xlog_cols and "status" not in xlog_cols:
        conn.execute("ALTER TABLE extraction_log ADD COLUMN status TEXT DEFAULT 'ok'")

    # 2026-10-13: confidence_signals records WHY self_consistency is null.
    sig_cols = {r["name"] for r in conn.execute("PRAGMA table_info(confidence_signals)")}
    if sig_cols and "self_consistency_reason" not in sig_cols:
        conn.execute("ALTER TABLE confidence_signals ADD COLUMN self_consistency_reason TEXT")

    # fix step 1: messages store the Q-A-resolved rewrite alongside the original.
    msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    if msg_cols and "resolved_content" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN resolved_content TEXT")

    # evidence gained attribute_id + nullable node_id: rebuild, preserving rows.
    ev_cols = {r["name"] for r in conn.execute("PRAGMA table_info(evidence)")}
    if ev_cols and "attribute_id" not in ev_cols:
        conn.execute("ALTER TABLE evidence RENAME TO evidence_old")
        conn.executescript(SCHEMA)  # recreates evidence with the new shape
        conn.execute(
            "INSERT INTO evidence (id, node_id, attribute_id, session_id, message_id, quote, created_at) "
            "SELECT id, node_id, NULL, session_id, message_id, quote, created_at FROM evidence_old"
        )
        conn.execute("DROP TABLE evidence_old")


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        # Upgrade from the legacy conversation-scoped schema: the data was
        # throwaway test conversations, so we rebuild rather than migrate.
        legacy = _table_exists(conn, "conversations") and not _table_exists(conn, "users")
        if legacy:
            conn.execute("PRAGMA foreign_keys = OFF")
            for tbl in _LEGACY_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        _migrate(conn)


def ensure_default_user(conn: sqlite3.Connection) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO users (id, label, created_at) VALUES (?, ?, ?)",
        (DEFAULT_USER_ID, DEFAULT_USER_LABEL, now_iso()),
    )
    return DEFAULT_USER_ID
