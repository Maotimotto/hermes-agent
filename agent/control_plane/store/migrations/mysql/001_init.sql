-- 001_init.sql (MySQL 8+)
-- Initial schema for Session Store on MySQL.
-- NOTE: Table names are bare (no prefix) — the MysqlDriver injects the
-- configured prefix (e.g. `hcp_`) at execution time. Same goes for the
-- index target tables and REFERENCES clauses.

CREATE TABLE IF NOT EXISTS sessions (
    id              VARCHAR(64) PRIMARY KEY,
    runtime_kind    VARCHAR(32) NOT NULL DEFAULT 'claude',
    model           VARCHAR(128),
    repo_path       TEXT,
    workspace_id    VARCHAR(128),
    status          VARCHAR(16) NOT NULL DEFAULT 'created',
    started_at      VARCHAR(40) NOT NULL,
    ended_at        VARCHAR(40),
    metadata        JSON,
    CONSTRAINT chk_sessions_status CHECK (
        status IN ('created','running','waiting','completed',
                   'failed','cancelled','stopped')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_sessions_status  ON sessions(status);
CREATE INDEX idx_sessions_started ON sessions(started_at);

CREATE TABLE IF NOT EXISTS turns (
    id              VARCHAR(64) PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    prompt          MEDIUMTEXT,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
    started_at      VARCHAR(40) NOT NULL,
    ended_at        VARCHAR(40),
    metadata        JSON,
    CONSTRAINT fk_turns_session
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    CONSTRAINT chk_turns_status CHECK (
        status IN ('pending','running','completed','failed','cancelled')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_turns_session ON turns(session_id);

CREATE TABLE IF NOT EXISTS events (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    turn_id         VARCHAR(64),
    type            VARCHAR(64) NOT NULL,
    payload         JSON NOT NULL,
    created_at      VARCHAR(40) NOT NULL,
    CONSTRAINT fk_events_session
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_events_turn
        FOREIGN KEY (turn_id) REFERENCES turns(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_events_session       ON events(session_id);
CREATE INDEX idx_events_session_turn  ON events(session_id, turn_id);
CREATE INDEX idx_events_type          ON events(type);

CREATE TABLE IF NOT EXISTS approvals (
    id              VARCHAR(64) PRIMARY KEY,
    session_id      VARCHAR(64) NOT NULL,
    turn_id         VARCHAR(64),
    action_kind     VARCHAR(64) NOT NULL,
    action_payload  JSON,
    risk            VARCHAR(16),
    decision        VARCHAR(16),
    decided_at      VARCHAR(40),
    decided_by      VARCHAR(64),
    ttl_until       VARCHAR(40),
    CONSTRAINT fk_approvals_session
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    CONSTRAINT fk_approvals_turn
        FOREIGN KEY (turn_id) REFERENCES turns(id) ON DELETE SET NULL,
    CONSTRAINT chk_approvals_decision CHECK (
        decision IS NULL OR decision IN ('approved','denied','pending')
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_approvals_session     ON approvals(session_id);
CREATE INDEX idx_approvals_decision    ON approvals(decision);
CREATE INDEX idx_approvals_action_kind ON approvals(action_kind);
