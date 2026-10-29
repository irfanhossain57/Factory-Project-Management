PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS junctions (
 id TEXT PRIMARY KEY,
 state_json TEXT NOT NULL,
 updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
 event_id TEXT PRIMARY KEY,
 junction_id TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 response_json TEXT NOT NULL,
 http_status INTEGER NOT NULL,
 received_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 junction_id TEXT NOT NULL,
 event_type TEXT NOT NULL,
 timestamp REAL NOT NULL,
 details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_junction_id ON history(junction_id,id DESC);
PRAGMA user_version=1;
