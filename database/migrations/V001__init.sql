-- =====================================================================
-- Codex Global Knowledge Base :: schema v1
-- SQLite 3.34+ (FTS5 trigram tokenizer required)
-- =====================================================================
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    notes      TEXT
);

-- ---------------------------------------------------------------- meta
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

-- -------------------------------------------------------------- assets
CREATE TABLE IF NOT EXISTS assets (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    category         TEXT NOT NULL,
    summary          TEXT,
    use_cases        TEXT,
    source_type      TEXT,
    source_url       TEXT,
    docs_url         TEXT,
    version          TEXT,
    license          TEXT,
    install_method   TEXT,
    install_command  TEXT,
    config_method    TEXT,
    invoke_method    TEXT,
    runtime_env      TEXT,
    permissions      TEXT,
    security_risk    TEXT,
    rollback_method  TEXT,
    install_status   TEXT,
    verify_status    TEXT,
    lifecycle_status TEXT,
    evidence_level   TEXT,
    confidence       INTEGER DEFAULT 0,
    path             TEXT,
    last_verified_at TEXT,
    last_checked_at  TEXT,
    usage_count      INTEGER DEFAULT 0,
    notes            TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (name, category)
);

CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
CREATE INDEX IF NOT EXISTS idx_assets_lifecycle ON assets(lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_assets_verify ON assets(verify_status);
CREATE INDEX IF NOT EXISTS idx_assets_evidence ON assets(evidence_level);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (asset_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag);

-- Dependency / relation graph: asset -> asset
CREATE TABLE IF NOT EXISTS asset_links (
    src_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    dst_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,   -- depends_on | provides | related | replaced_by | uses
    note     TEXT,
    PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_asset_links_dst ON asset_links(dst_id);

-- Lifecycle / audit history
CREATE TABLE IF NOT EXISTS asset_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,   -- discovered | installed | configured | verified | failed | updated | deprecated | uninstalled | replaced | noted
    detail     TEXT,
    actor      TEXT,
    happened_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_events_asset ON asset_events(asset_id);

CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT REFERENCES assets(id) ON DELETE CASCADE,
    check_name  TEXT NOT NULL,
    command     TEXT,
    result      TEXT NOT NULL,  -- pass | fail | skip
    output      TEXT,
    verified_at TEXT NOT NULL
);

-- ------------------------------------------------------------- history
CREATE TABLE IF NOT EXISTS history_sources (
    id           TEXT PRIMARY KEY,   -- codex_sessions | codex_thread_db | dsh_sessions | chatgpt_export | ...
    label        TEXT NOT NULL,
    kind         TEXT,               -- codex | dsh | chatgpt | local | github
    location     TEXT,
    status       TEXT NOT NULL,      -- scanned | partial | inaccessible | missing
    items        INTEGER DEFAULT 0,
    threads      INTEGER DEFAULT 0,
    bytes        INTEGER DEFAULT 0,
    scanned_at   TEXT,
    detail       TEXT
);

CREATE TABLE IF NOT EXISTS history_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    thread_id   TEXT,
    thread_title TEXT,
    cwd         TEXT,
    ts          TEXT,
    kind        TEXT NOT NULL,   -- command | mcp | file | websearch | message | meta
    name        TEXT,
    detail      TEXT,
    status      TEXT,
    extra       TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_items_kind ON history_items(kind);
CREATE INDEX IF NOT EXISTS idx_history_items_thread ON history_items(thread_id);
CREATE INDEX IF NOT EXISTS idx_history_items_name ON history_items(name);

CREATE TABLE IF NOT EXISTS commands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature     TEXT NOT NULL UNIQUE,
    tool          TEXT,
    verb          TEXT,
    sample        TEXT,
    uses          INTEGER DEFAULT 1,
    ok_count      INTEGER DEFAULT 0,
    fail_count    INTEGER DEFAULT 0,
    cwds          TEXT,
    risk          TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_tool ON commands(tool);

CREATE TABLE IF NOT EXISTS errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signature    TEXT NOT NULL UNIQUE,
    error_text   TEXT,
    occurrences  INTEGER DEFAULT 1,
    first_seen   TEXT,
    last_seen    TEXT,
    resolved     INTEGER DEFAULT 0,
    fix_summary  TEXT,
    fix_commands TEXT,
    related_asset TEXT,
    notes        TEXT
);

-- ----------------------------------------------------------------- docs
CREATE TABLE IF NOT EXISTS docs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL UNIQUE,
    title     TEXT,
    category  TEXT,
    body      TEXT,
    updated_at TEXT
);

-- ------------------------------------------------------------------ fts
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
    id UNINDEXED,
    name,
    category,
    summary,
    use_cases,
    invoke_method,
    notes,
    tags,
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
    id UNINDEXED,
    name,
    detail,
    thread_title,
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    path UNINDEXED,
    title,
    body,
    tokenize = 'trigram'
);

-- ----------------------------------------------------- ops / audit logs
CREATE TABLE IF NOT EXISTS sync_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    action    TEXT NOT NULL,
    status    TEXT NOT NULL,
    detail    TEXT,
    happened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL,
    month       TEXT NOT NULL,
    status      TEXT NOT NULL,
    checks_json TEXT,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS llm_memory (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,          -- global | project:<path>
    kind       TEXT NOT NULL,          -- preference | convention | lesson
    content    TEXT NOT NULL,
    weight     INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (scope, kind, content)
);
