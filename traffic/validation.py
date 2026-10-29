import math
import re
from datetime import datetime
from copy import deepcopy
from .domain import DIRECTIONS, TYPES, DEFAULT_CONFIG, DomainError


def require(condition, message):
    if not condition:
        raise DomainError(message, 422)


def text(data, key):
    value = data.get(key)
    require(isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value, key + ' must be a nonempty string (max 128 characters)')
    return value


def timestamp(data):
    value = text(data, 'timestamp')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(dt.tzinfo is not None, 'timestamp must include a timezone')
        return dt.timestamp()
    except (ValueError, OverflowError):
        raise DomainError('timestamp must be ISO 8601 with timezone', 422)


def event(data, device=False):
    text(data, 'event_id')
    text(data, 'junction_id')
    if device:
        require(data.get('device_type') in ('SIGNAL_CONTROLLER', 'SIGNAL', 'SENSOR'), 'Invalid device_type')
        require(data.get('status') in ('ONLINE', 'OFFLINE', 'UNKNOWN', 'DEGRADED'), 'Invalid device status')
        require(data.get('direction') in DIRECTIONS or 'direction' not in data and data['device_type'] == 'SIGNAL_CONTROLLER', 'Invalid direction')
    else:
        require(data.get('direction') in DIRECTIONS, 'Invalid direction')
        require(data.get('event_type') in ('VEHICLE_ARRIVED', 'VEHICLE_CLEARED'), 'Invalid event_type')
        text(data, 'vehicle_id')
        require(type(data.get('sequence_no')) is int and 0 <= data['sequence_no'] <= 2**53 - 1, 'sequence_no must be a nonnegative safe integer')
        if data['event_type'] == 'VEHICLE_ARRIVED' or 'vehicle_type' in data:
            require(isinstance(data.get('vehicle_type'), str) and data['vehicle_type'] in TYPES, 'Invalid vehicle_type')
    return timestamp(data)


def command(data):
    require(data.get('command') in ('MANUAL_GREEN_REQUEST', 'RETURN_TO_AUTOMATIC', 'RECOVER', 'RECONCILE'), 'Invalid command')
    if data['command'] == 'MANUAL_GREEN_REQUEST':
        require(data.get('direction') in DIRECTIONS, 'Invalid direction')


def acknowledgement(data):
    text(data, 'junction_id')
    text(data, 'command_id')
    require(data.get('status') in ('ACK', 'NACK'), 'Invalid ACK status')
    if data['status'] == 'ACK':
        require(data.get('actual_state') in ('RED', 'YELLOW', 'GREEN', 'UNKNOWN'), 'Invalid actual_state')


def junction(data):
    name = text(data, 'junction_id')
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,40}', name) is not None, 'junction_id must use letters, digits, underscore or hyphen')
    overrides = data.get('config', {})
    require(isinstance(overrides, dict), 'config must be an object')
    require(not set(overrides) - set(DEFAULT_CONFIG), 'Unknown configuration key')
    result = deepcopy(DEFAULT_CONFIG)
    result.update(overrides)
    phases = result['phases']
    require(isinstance(phases, dict) and len(phases) == 2, 'Exactly two phases required')
    for p, ds in phases.items():
        require(isinstance(p, str) and re.fullmatch(r'[A-Z_]{1,40}', p) is not None, 'Invalid phase name')
        require(isinstance(ds, list) and all(isinstance(d, str) for d in ds), 'Phase directions must be a list')
    groups = {frozenset(ds) for ds in phases.values()}
    require(groups == {frozenset(('NORTH', 'SOUTH')), frozenset(('EAST', 'WEST'))} and all(len(ds) == 2 for ds in phases.values()), 'Phases must partition NORTH/SOUTH and EAST/WEST')
    for key, value in result.items():
        if key == 'phases':
            continue
        require(type(value) in (int, float) and math.isfinite(value) and 1 <= value <= 3600, key + ' must be 1..3600 seconds')
    require(result['yellow_seconds'] >= 5, 'yellow_seconds must be at least 5')
    require(result['clearance_seconds'] >= 2, 'clearance_seconds must be at least 2')
    return name, result
