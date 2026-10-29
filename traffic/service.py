import hashlib
import json
import time
from .domain import Engine, DomainError, DEFAULT_CONFIG
from . import validation as v


class Service:
    def __init__(self, repository, clock=time.time):
        self.repo = repository
        self.clock = clock

    def boot(self):
        with self.repo.transaction() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM junctions')]
            if not ids:
                self.repo.save(db, Engine.create('A', DEFAULT_CONFIG, self.clock()))
            for jid in ids:
                e = self.engine(db, jid)
                e.restart()
                self.repo.save(db, e)

    def engine(self, db, jid):
        state = self.repo.get(db, jid)
        if state is None:
            raise DomainError('Unknown junction', 404)
        return Engine(state, self.clock())

    def list(self):
        with self.repo.connection() as db:
            return [Engine(json.loads(r[0]), self.clock()).status() for r in db.execute('SELECT state_json FROM junctions ORDER BY id')]

    def status(self, jid):
        with self.repo.connection() as db:
            return self.engine(db, jid).status()

    def create(self, data):
        jid, config = v.junction(data)
        with self.repo.transaction() as db:
            if self.repo.get(db, jid):
                raise DomainError('Junction already exists')
            e = Engine.create(jid, config, self.clock())
            self.repo.save(db, e)
            return e.status()

    def event(self, data, device=False):
        # Validation errors are also audited; well-formed event IDs are durably deduplicated.
        digest = hashlib.sha256(json.dumps({'device': device, 'payload': data}, sort_keys=True).encode()).hexdigest()
        eid = data.get('event_id')
        durable_id = isinstance(eid, str) and 0 < len(eid) <= 128
        jid = data.get('junction_id') if isinstance(data.get('junction_id'), str) else '_invalid'
        with self.repo.transaction() as db:
            prior = db.execute('SELECT * FROM receipts WHERE event_id=?', (eid,)).fetchone() if durable_id else None
            if prior:
                conflict = prior['payload_hash'] != digest
                self.repo.audit(db, jid, {'event_type': 'REJECTED_EVENT_ID_REUSE' if conflict else 'DUPLICATE_SENSOR_EVENT', 'timestamp': self.clock(), 'event_id': eid})
                if conflict:
                    return {'error': 'event_id reused with a different payload'}, 409
                return {**json.loads(prior['response_json']), 'duplicate': True}, prior['http_status']
            try:
                sensor_time = v.event(data, device)
                e = self.engine(db, jid)
                if device:
                    e.device(data, sensor_time)
                else:
                    e.sensor(data, sensor_time)
                self.repo.save(db, e)
                result, code = {'accepted': True, 'duplicate': False, 'event_id': eid}, 200
            except DomainError as exc:
                result, code = {'error': str(exc), 'event_id': eid}, exc.status
                self.repo.audit(db, jid, {'event_type': 'REJECTED_SENSOR_EVENT', 'timestamp': self.clock(), 'reason': str(exc), 'event_id': eid})
            if durable_id:
                db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)', (eid, jid, digest, json.dumps(result), code, self.clock()))
            return result, code

    def command(self, jid, data):
        v.command(data)
        with self.repo.transaction() as db:
            e = self.engine(db, jid)
            e.command(data)
            self.repo.save(db, e)
            return e.status()

    def ack(self, data):
        v.acknowledgement(data)
        with self.repo.transaction() as db:
            e = self.engine(db, data['junction_id'])
            e.ack(data)
            e.tick()
            self.repo.save(db, e)
            return e.status()

    def tick(self):
        with self.repo.transaction() as db:
            for row in db.execute('SELECT state_json FROM junctions').fetchall():
                state = json.loads(row[0])
                e = Engine(state, self.clock())
                e.tick()
                if state != e.s or e.logs:
                    self.repo.save(db, e)

    def history(self, jid, limit=100, before=None):
        with self.repo.connection() as db:
            self.engine(db, jid)
            rows = db.execute('SELECT * FROM history WHERE junction_id=? AND id<? ORDER BY id DESC LIMIT ?',
                              (jid, before or 2**63 - 1, limit)).fetchall()
            return [{'id': r['id'], **json.loads(r['details_json'])} for r in rows]
