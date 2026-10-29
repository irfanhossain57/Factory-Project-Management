"""SQLite unit of work; domain snapshots, outbox, dedup and audit commit together."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Repository:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(Path(__file__).with_name('schema.sql').read_text())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def get(self, db, junction_id):
        row = db.execute('SELECT state_json FROM junctions WHERE id=?', (junction_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, db, engine):
        s = engine.s
        db.execute('INSERT INTO junctions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at',
                   (s['junction_id'], json.dumps(s), engine.now))
        for event in engine.logs:
            self.audit(db, s['junction_id'], event)

    def audit(self, db, junction_id, event):
        db.execute('INSERT INTO history(junction_id,event_type,timestamp,details_json) VALUES(?,?,?,?)',
                   (junction_id, event['event_type'], event['timestamp'], json.dumps(event)))
