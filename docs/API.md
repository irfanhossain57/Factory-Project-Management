# REST API reference

Base URL: `http://127.0.0.1:8000`. All POST requests require `Content-Type: application/json`. Maximum body is 64 KiB. Responses are JSON; errors contain `error`. Timestamps in input are ISO 8601 with timezone; server output deadlines/audit timestamps are UTC Unix seconds. No authentication in this local assessment.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | Process liveness |
| GET | `/api/junctions` | Array of junction status objects |
| POST | `/api/junctions` | Create a junction; 201 |
| GET | `/api/junctions/{id}` | Junction configuration and status |
| GET | `/api/junctions/{id}/status` | Same status representation |
| POST | `/api/sensor-events` | Accept/reject/deduplicate vehicle event |
| POST | `/api/device-events` | Additional endpoint for device status |
| POST | `/api/junctions/{id}/commands` | Command intent; updated status |
| POST | `/api/controller-events` | ACK/NACK; updated status |
| GET | `/api/junctions/{id}/history?limit=100&before=123` | Newest-first audit; limit 1..200, optional exclusive history ID cursor |

## Create junction

```json
{"junction_id":"B","config":{"green_seconds":30,"yellow_seconds":5,"clearance_seconds":2}}
```

IDs: letters/digits/underscore/hyphen, 1..40 characters. Defaults: phases NORTH_SOUTH=[NORTH,SOUTH], EAST_WEST=[EAST,WEST]; green=30s, yellow=5s, clearance=2s, ACK timeout=8s, manual=120s, emergency=120s, maximum wait=90s. All durations must be finite numbers in 1..3600; yellow minimum 5 and clearance minimum 2. Only compatible opposite-direction pairs are accepted. Initial state is DEGRADED pending all-red ACK and Recover.

## Arrival / clearance

Generate a fresh timestamp at submission; old example timestamps are intentionally rejected.

```json
{"event_id":"arrival-001","junction_id":"A","direction":"NORTH","event_type":"VEHICLE_ARRIVED","vehicle_id":"VH-501","vehicle_type":"TRUCK","sequence_no":1,"timestamp":"2026-10-28T00:00:00Z"}
```

```json
{"event_id":"clear-001","junction_id":"A","direction":"NORTH","event_type":"VEHICLE_CLEARED","vehicle_id":"VH-501","sequence_no":2,"timestamp":"2026-10-28T00:00:01Z"}
```

`vehicle_type` is mandatory for arrivals, optional for clearances; if supplied it must be valid. Types: EMPLOYEE_VEHICLE, FORKLIFT, TRUCK, EMERGENCY. Directions: NORTH, SOUTH, EAST, WEST. Sequence is an integer 0..9007199254740991, strictly increasing per vehicle/junction. Event and vehicle IDs are nonempty strings up to 128 characters. Events are accepted within 300 seconds past / 30 seconds future. Accepted response: `{"accepted":true,"duplicate":false,"event_id":"arrival-001"}`. Exact repetition sets duplicate=true; different payload with the same ID returns 409.

## Control intents

```json
{"command":"MANUAL_GREEN_REQUEST","direction":"WEST"}
```

Other commands:

```json
{"command":"RETURN_TO_AUTOMATIC"}
{"command":"RECONCILE"}
{"command":"RECOVER"}
```

RECONCILE is permitted only in DEGRADED and reissues all-red commands. RECOVER requires an ONLINE controller, all reported devices ONLINE, confirmed all-red signals and no pending commands. Manual requests are rejected while degraded. Return-to-automatic cancels a manual lease without clearing faults or active emergencies.

## Device status

```json
{"event_id":"device-001","junction_id":"A","device_type":"SIGNAL_CONTROLLER","direction":"SOUTH","status":"OFFLINE","timestamp":"2026-10-28T00:00:00Z"}
```

Device types: SIGNAL_CONTROLLER, SIGNAL, SENSOR. Status: ONLINE, OFFLINE, UNKNOWN, DEGRADED. Direction is required except for SIGNAL_CONTROLLER. A controller report is junction-wide even if a direction is supplied; return ONLINE using the same device key to restore that reported device. Other failed device keys must also be restored. Freshness and event-ID rules match vehicle events; device statuses must have strictly increasing timestamps per type/direction.

## Controller outbox and acknowledgement

Read `pending_commands` from junction status. Each command has command_id, junction_id, direction, requested_state, epoch, issued_at and expires_at. The ID-keyed object is durable. Never invent or reuse an older ID to acknowledge a new command.

```json
{"command_id":"<pending-command-id>","junction_id":"A","status":"ACK","actual_state":"RED"}
```

`ACK` requires actual_state RED, YELLOW, GREEN or UNKNOWN. It must equal the requested state or the junction faults. `NACK` needs no actual_state. Acknowledgements are timestamped on server receipt; a client timestamp, if supplied, is informational and does not extend command expiry. No ACK before expiry means timeout. Exact duplicate ACKs for completed IDs do not advance timers again. Unknown command ID returns 404. Failure ACKs return HTTP 200 with the resulting DEGRADED state: the message was processed, but the operation failed physically.

## Status representation

Contains `junction_id`, `config`, `mode`, `phase`, `stage`, `controller_status`, `devices`, `desired_signals`, `actual_signals`, `queues`, `vehicles`, `emergencies`, `manual`, `fault`, `alerts`, `pending_commands`, `epoch`, `deadline`, `last_served`, `updated_at` and optional `device_times`. Actual signals may be UNKNOWN. Phase is null in ALL_RED. An emergency can be queued while DEGRADED; the failure latch has higher precedence.

## Errors

| Code | Meaning |
| --- | --- |
| 400 | Malformed JSON or Content-Length |
| 404 | Unknown route, junction or command ID |
| 409 | Unsafe operation, stale sequence, conflicting event ID, duplicate vehicle or invalid recovery |
| 413 | Empty/oversized JSON body |
| 415 | Content type is not application/json |
| 422 | Missing/invalid field, unsafe config or timestamp outside accepted window |
| 500 | Unexpected server error; internal details stay in logs |

Rejected identified sensor/device events preserve their original error response on duplicate submissions. History records them even if no queue change occurred.
