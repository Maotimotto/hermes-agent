-- 001_init.sql
-- Initial schema for Session Store
-- Tables: sessions, turns, events, approvals + migrations tracker

-- ─── Sessions ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    runtime_kind    TEXT NOT NULL DEFAULT 'claude',
    model           TEXT,
    repo_path       TEXT,
    workspace_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'created'
        CHECK(status IN ('created', 'running', 'waiting', 'completed',
                         'failed', 'cancelled', 'stopped')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    metadata        TEXT   -- JSON
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

-- ─── Turns ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS turns (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    prompt          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    metadata        TEXT   -- JSON
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);

-- ─── Events ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id         TEXT REFERENCES turns(id) ON DELETE SET NULL,
    type            TEXT NOT NULL,
    payload         TEXT NOT NULL,  -- JSON
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session_turn ON events(session_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

-- ─── Approvals ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id         TEXT REFERENCES turns(id) ON DELETE SET NULL,
    action_kind     TEXT NOT NULL,
    action_payload  TEXT,  -- JSON
    risk            TEXT,
    decision        TEXT CHECK(decision IN ('approved', 'denied', 'pending')),
    decided_at      TEXT,
    decided_by      TEXT,
    ttl_until       TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_session ON approvals(session_id);
CREATE INDEX IF NOT EXISTS idx_approvals_decision ON approvals(decision);
CREATE INDEX IF NOT EXISTS idx_approvals_action_kind ON approvals(action_kind);
