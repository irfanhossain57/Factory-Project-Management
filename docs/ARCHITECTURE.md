# Architecture and invariants

## Modules

| Module | Responsibility |
| --- | --- |
| `domain.py` | State machine, scoring, leases, failures, ACK barriers, domain audit events |
| `validation.py` | Boundary validation, timestamp parsing, safe configuration |
| `service.py` | Application operations, deduplication, transactional persistence |
| `repository.py` / `schema.sql` | SQLite initialization, state snapshots, receipts, append-only audit |
| `controller.py` | Controller protocol and optional simulated adapter |
| `server.py` | REST routing, HTTP errors, static files, scheduler and process lock |
| `static/` | Backend-driven polling dashboard |

The call path is HTTP → Service → Engine → Repository. The scheduler invokes Service.tick. The simulator consumes the durable command outbox and calls Service.ack; a future MQTT adapter can do the same. Neither the browser nor transport adapter owns transition rules.

## Persisted aggregate

Each junction snapshot contains configuration, stage, phase, mode, actual and desired lamps, pending/completed controller commands, command epoch, deadline, fault latch, device statuses/timestamps, manual lease, per-vehicle queue entries/sequences and last-served times. Pending command objects are the outbox. The receipt table preserves canonical payload SHA-256 and original response/status. Audit entries append in the same SQLite transaction as the state transition.

A single `BEGIN IMMEDIATE` write transaction serializes each operation. HTTP threads and the scheduler cannot read-modify-write the same state concurrently. GET uses committed snapshots; separate status/history requests may observe adjacent committed versions, which is acceptable for this dashboard. Read-modify-write operations all occur inside a transaction. Failed domain operations discard the copied engine state; rejected sensor receipts and rejection audit events still commit.

## Safety properties

- A desired signal map is generated only by the engine from a validated compatible phase.
- GREEN never transitions directly to the conflicting GREEN.
- All four direction ACKs complete before the stage timer starts.
- A conflicting GREEN requires a confirmed all-red barrier plus clearance.
- Failure or restart invalidates actual state and prevents new GREEN requests.
- Old/superseded command IDs cannot complete a current ACK barrier.
- Queues contain unique vehicle IDs; counts are derived, never decremented blindly.
- Every accepted/rejected identified event has a durable deduplication receipt.

The `force` argument in request indicates reconciliation intent for readability; each request always emits a complete four-direction batch, even if the desired colors have not changed.

## Required physical-controller contract

REST simulation cannot enforce physical safety by itself. A real controller must:

1. Enforce electrical/firmware mutual exclusion of incompatible greens locally.
2. Persist the highest accepted epoch and reject lower epochs. Process a command ID idempotently; commands for the four directions in the same epoch may arrive in any order.
3. Reject commands after `expires_at`; enforce minimum yellow/all-red durations locally.
4. Send ACK only after applying and verifying the requested lamp state. Send NACK/UNKNOWN for a failed or unobservable lamp.
5. Enter its local fail-safe behavior on communication loss and provide fresh status after reconnect.

The simulator has no packet transport or hardware and therefore acknowledges only the current durable outbox. A simulated ACK proves only the test adapter's response, not real lamp behavior. A stale telemetry report for a superseded command is audited but ignored for current state; a production adapter must also provide epoch-tagged fresh telemetry and detect physical conflicts independently.

## Failure flow

A timeout or NACK latches DEGRADED, marks confirmed lamps UNKNOWN and issues a new all-red batch. If all-red itself cannot be confirmed, the backend leaves the fault latched and stops issuing commands. Reconcile explicitly reissues that batch. Reconnect also requests reconciliation. Recover is accepted only with every reported device ONLINE, no pending commands and all lamps confirmed RED. It cancels any manual lease and starts a fresh clearance timer. Unexpired emergency queues remain available for scheduling.

## Adding a junction or MQTT

Use POST /api/junctions with a unique ID and optional validated configuration. No engine changes are necessary. To add MQTT, implement ControllerPort.poll or an outbox-dispatch worker, publish command objects on junction-scoped topics, validate authenticated responses and call Service.ack / Service.event. Do not put traffic logic in MQTT callbacks. Multi-process dispatch requires replacing the current lifetime lock/SQLite approach with proper single-owner fencing or leader election.
