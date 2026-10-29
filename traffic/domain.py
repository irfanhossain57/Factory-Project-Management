"""Deterministic traffic domain. No network, database, threads or wall clock."""
from copy import deepcopy
import uuid

DIRECTIONS = ('NORTH', 'SOUTH', 'EAST', 'WEST')
TYPES = {'EMPLOYEE_VEHICLE': 1, 'FORKLIFT': 2, 'TRUCK': 4, 'EMERGENCY': 20}
DEFAULT_CONFIG = {
    'phases': {'NORTH_SOUTH': ['NORTH', 'SOUTH'], 'EAST_WEST': ['EAST', 'WEST']},
    'green_seconds': 30, 'yellow_seconds': 5, 'clearance_seconds': 2,
    'ack_timeout_seconds': 8, 'manual_seconds': 120,
    'emergency_seconds': 120, 'max_wait_seconds': 90,
}


class DomainError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class Engine:
    def __init__(self, state, now):
        self.s = deepcopy(state)
        self.now = now
        self.logs = []

    def log(self, kind, **details):
        self.logs.append({**details, 'event_type': kind, 'timestamp': self.now})

    @classmethod
    def create(cls, junction_id, config, now):
        state = {'junction_id': junction_id, 'config': deepcopy(config),
                 'mode': 'DEGRADED', 'stage': 'ALL_RED', 'phase': None,
                 'desired_signals': dict.fromkeys(DIRECTIONS, 'RED'),
                 'actual_signals': dict.fromkeys(DIRECTIONS, 'UNKNOWN'),
                 'controller_status': 'ONLINE', 'devices': {},
                 'pending_commands': {}, 'completed_commands': {}, 'epoch': 0,
                 'vehicles': {}, 'sequences': {}, 'manual': None,
                 'fault': 'Initialization: confirm all RED, then recover',
                 'deadline': None, 'last_served': {}, 'updated_at': now}
        e = cls(state, now)
        e.request('ALL_RED', None, force=True)
        e.log('JUNCTION_CREATED')
        return e

    def phase_for(self, direction):
        return next(p for p, ds in self.s['config']['phases'].items() if direction in ds)

    def request(self, stage, phase, force=False):
        s = self.s
        s['epoch'] += 1
        # Superseded IDs remain known, but can never advance the current barrier.
        for cid, cmd in s['pending_commands'].items():
            s['completed_commands'][cid] = {**cmd, 'result': 'SUPERSEDED'}
        s['pending_commands'] = {}
        s['stage'], s['phase'], s['deadline'] = stage, phase, None
        desired = dict.fromkeys(DIRECTIONS, 'RED')
        if stage in ('GREEN', 'YELLOW'):
            for direction in s['config']['phases'][phase]:
                desired[direction] = stage
        s['desired_signals'] = desired
        # Full-state barrier: even unchanged RED lamps must be confirmed.
        for direction, requested in desired.items():
            cid = str(uuid.uuid4())
            cmd = {'command_id': cid, 'junction_id': s['junction_id'],
                   'direction': direction, 'requested_state': requested,
                   'epoch': s['epoch'], 'issued_at': self.now,
                   'expires_at': self.now + s['config']['ack_timeout_seconds']}
            s['pending_commands'][cid] = cmd
            self.log('SIGNAL_STATE_REQUESTED', **cmd)
        self.log('SIGNAL_TRANSITION_STARTED', stage=stage, phase=phase)

    def fail(self, reason):
        if self.s['fault'] == reason:
            return
        self.s['fault'] = reason
        self.s['mode'] = 'DEGRADED'
        self.s['actual_signals'] = dict.fromkeys(DIRECTIONS, 'UNKNOWN')
        self.log('FAILURE', reason=reason)
        self.request('ALL_RED', None, force=True)

    def restart(self):
        self.s['fault'] = None
        self.fail('Backend restarted: physical state requires reconciliation')
        self.log('RESTART_RECOVERY')

    def ack(self, data):
        s = self.s
        cid = data['command_id']
        cmd = s['pending_commands'].get(cid)
        if cmd is None:
            prior = s['completed_commands'].get(cid)
            if prior:
                # A conflicting report on a previously successful command is a fault.
                if prior['result'] == 'ACK' and (data['status'] != 'ACK' or data.get('actual_state') != prior['requested_state']):
                    self.fail('Conflicting duplicate controller acknowledgement')
                self.log('DUPLICATE_OR_STALE_ACK', command_id=cid)
                return
            raise DomainError('Unknown command_id', 404)
        if self.now >= cmd['expires_at']:
            self.fail('Controller acknowledgement timeout')
            self.log('CONTROLLER_TIMEOUT', command_id=cid)
            return
        self.log('CONTROLLER_ACKNOWLEDGEMENT', command_id=cid, status=data['status'])
        if data['status'] != 'ACK' or data.get('actual_state') != cmd['requested_state']:
            self.fail('Controller NACK or desired/actual mismatch')
            return
        if s['controller_status'] != 'ONLINE':
            raise DomainError('Controller must report ONLINE before acknowledgement')
        s['actual_signals'][cmd['direction']] = data['actual_state']
        s['completed_commands'][cid] = {**cmd, 'result': 'ACK'}
        del s['pending_commands'][cid]
        self.log('SIGNAL_STATE_CONFIRMED', direction=cmd['direction'], state=data['actual_state'], command_id=cid)
        if not s['pending_commands']:
            duration = {'GREEN': 'green_seconds', 'YELLOW': 'yellow_seconds', 'ALL_RED': 'clearance_seconds'}[s['stage']]
            s['deadline'] = self.now + s['config'][duration]
            if s['stage'] == 'GREEN':
                s['last_served'][s['phase']] = self.now

    def sensor(self, data, sensor_time):
        s = self.s
        key = data['vehicle_id']
        previous = s['sequences'].get(key, -1)
        if data['sequence_no'] <= previous:
            raise DomainError('Stale sequence_no for this vehicle')
        if sensor_time > self.now + 30 or sensor_time < self.now - 300:
            raise DomainError('Sensor timestamp outside 5-minute past / 30-second future window', 422)
        existing = s['vehicles'].get(key)
        if data['event_type'] == 'VEHICLE_ARRIVED':
            if existing:
                raise DomainError('Vehicle already queued; clear it before another arrival')
            s['vehicles'][key] = {'vehicle_id': key, 'direction': data['direction'],
                                  'vehicle_type': data['vehicle_type'], 'arrived_at': self.now,
                                  'sensor_timestamp': data['timestamp'], 'emergency_expired': False}
            self.log('VEHICLE_DETECTED', payload=data)
            if data['vehicle_type'] == 'EMERGENCY':
                self.log('EMERGENCY_DETECTED', vehicle_id=key, direction=data['direction'])
        else:
            if existing and existing['direction'] != data['direction']:
                raise DomainError('Clearance direction differs from queued vehicle')
            if existing:
                del s['vehicles'][key]
                self.log('VEHICLE_CLEARED', vehicle_id=key, direction=data['direction'])
                if existing['vehicle_type'] == 'EMERGENCY':
                    self.log('EMERGENCY_CLEARED', vehicle_id=key)
            else:
                self.log('UNMATCHED_CLEARANCE', vehicle_id=key)
        # Tombstones prevent an older arrival resurrecting an already cleared vehicle.
        s['sequences'][key] = data['sequence_no']
        self.tick()

    def command(self, data):
        s = self.s
        name = data['command']
        if name == 'MANUAL_GREEN_REQUEST':
            if s['fault']:
                raise DomainError('Recover the junction before manual control')
            s['manual'] = {'phase': self.phase_for(data['direction']),
                           'expires_at': self.now + s['config']['manual_seconds']}
            self.log('MANUAL_OVERRIDE', **s['manual'])
        elif name == 'RETURN_TO_AUTOMATIC':
            s['manual'] = None
            self.log('RETURN_TO_AUTOMATIC')
        elif name == 'RECOVER':
            if s['controller_status'] != 'ONLINE' or any(v != 'ONLINE' for v in s['devices'].values()):
                raise DomainError('All failed devices must report ONLINE first')
            if s['pending_commands'] or set(s['actual_signals'].values()) != {'RED'}:
                raise DomainError('All RED commands must be acknowledged before recovery')
            s['fault'] = None
            s['manual'] = None
            s['deadline'] = self.now + s['config']['clearance_seconds']
            self.log('RECOVERY_APPROVED')
        elif name == 'RECONCILE':
            if not s['fault']:
                raise DomainError('Reconciliation is only available in DEGRADED mode')
            self.request('ALL_RED', None, force=True)
            self.log('RECONCILIATION_REQUESTED')
        self.tick()

    def device(self, data, sensor_time):
        if sensor_time > self.now + 30 or sensor_time < self.now - 300:
            raise DomainError('Device timestamp outside accepted window', 422)
        s = self.s
        key = data['device_type'] + ':' + data.get('direction', 'ALL')
        old_time = s.setdefault('device_times', {}).get(key, -1)
        if sensor_time <= old_time:
            raise DomainError('Stale device status timestamp')
        s['device_times'][key] = sensor_time
        s['devices'][key] = data['status']
        if data['device_type'] == 'SIGNAL_CONTROLLER':
            s['controller_status'] = data['status']
        self.log('DEVICE_STATUS', payload=data)
        if data['status'] != 'ONLINE':
            self.fail(key + ' ' + data['status'])
        elif s['fault']:
            self.request('ALL_RED', None, force=True)

    def choose(self):
        s = self.s
        vehicles = list(s['vehicles'].values())
        emergencies = sorted((v for v in vehicles if v['vehicle_type'] == 'EMERGENCY' and not v['emergency_expired']),
                             key=lambda v: (v['arrived_at'], v['vehicle_id']))
        if emergencies:
            # FIFO among emergencies; timeout bounds each reservation.
            return 'EMERGENCY', self.phase_for(emergencies[0]['direction'])
        if s['manual']:
            return 'MANUAL', s['manual']['phase']
        if not vehicles:
            return 'AUTOMATIC', None
        scores, overdue = {}, {}
        for p, directions in s['config']['phases'].items():
            vs = [v for v in vehicles if v['direction'] in directions]
            if not vs:
                continue
            # Waiting since last green service, not total age of uncleared detections.
            waits = [self.now - max(v['arrived_at'], s['last_served'].get(p, 0)) for v in vs]
            scores[p] = sum(TYPES[v['vehicle_type']] for v in vs) + max(waits) / 5
            if max(waits) >= s['config']['max_wait_seconds']:
                overdue[p] = max(waits)
        candidates = overdue or scores
        target = max(candidates, key=lambda p: (candidates[p], p == s['phase'], p))
        return 'AUTOMATIC', target

    def tick(self):
        s = self.s
        for v in s['vehicles'].values():
            if v['vehicle_type'] == 'EMERGENCY' and not v['emergency_expired'] and self.now - v['arrived_at'] >= s['config']['emergency_seconds']:
                v['emergency_expired'] = True
                self.log('EMERGENCY_EXPIRED', vehicle_id=v['vehicle_id'])
        if s['manual'] and self.now >= s['manual']['expires_at']:
            s['manual'] = None
            self.log('MANUAL_EXPIRED')
        if any(self.now >= c['expires_at'] for c in s['pending_commands'].values()):
            if s['fault']:
                for cid, cmd in s['pending_commands'].items():
                    s['completed_commands'][cid] = {**cmd, 'result': 'TIMEOUT'}
                s['pending_commands'] = {}
                s['actual_signals'] = dict.fromkeys(DIRECTIONS, 'UNKNOWN')
                s['deadline'] = None
                self.log('CONTROLLER_TIMEOUT', reason='All-red reconciliation unconfirmed; explicit retry required')
            else:
                self.fail('Controller acknowledgement timeout')
                self.log('CONTROLLER_TIMEOUT')
        if s['fault']:
            s['mode'] = 'DEGRADED'
            return
        mode, target = self.choose()
        if mode != s['mode']:
            self.log('MODE_CHANGED', previous=s['mode'], new=mode)
            s['mode'] = mode
        # Never supersede a partially acknowledged normal transition.
        if s['pending_commands']:
            return
        deadline = s['deadline']
        if s['stage'] == 'GREEN':
            urgent = mode in ('MANUAL', 'EMERGENCY')
            if target != s['phase'] and (urgent or deadline is not None and self.now >= deadline):
                self.request('YELLOW', s['phase'])
            elif deadline is not None and self.now >= deadline:
                s['last_served'][s['phase']] = self.now
                s['deadline'] = self.now + s['config']['green_seconds']
        elif s['stage'] == 'YELLOW' and deadline is not None and self.now >= deadline:
            self.request('ALL_RED', None)
        elif s['stage'] == 'ALL_RED' and target and deadline is not None and self.now >= deadline:
            self.request('GREEN', target)
        s['updated_at'] = self.now

    def status(self):
        s = deepcopy(self.s)
        s['queues'] = {d: sum(v['direction'] == d for v in s['vehicles'].values()) for d in DIRECTIONS}
        s['alerts'] = ([s['fault']] if s['fault'] else [])
        if s['desired_signals'] != s['actual_signals']:
            s['alerts'].append('Desired/actual mismatch or unknown state')
        s['emergencies'] = [v for v in s['vehicles'].values() if v['vehicle_type'] == 'EMERGENCY' and not v['emergency_expired']]
        s.pop('completed_commands')
        s.pop('sequences')
        return s
