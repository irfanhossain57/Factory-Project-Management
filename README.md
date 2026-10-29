# Factory Traffic Management System

A complete local assessment application for Junction A and additional junctions. Includes a Python REST backend, SQLite persistence, an explicit traffic state machine, a browser dashboard, a REST controller simulator, audit history, and automated tests. Python standard library only; no package installation or build step is required.

## Setup and run

Requires Python **3.11 or later** and a recent Chrome, Edge, Firefox or Safari browser.

```bash
cd factory-traffic
python -m traffic --simulate
```

On Windows, use `py -3 -m traffic --simulate` if `python` is unavailable. Open **http://127.0.0.1:8000**. The simulated controller confirms the initial all-red commands automatically. Click **Recover after all-red ACK** to start automatic control. Add vehicles through the dashboard.

Without `--simulate`, the evaluator acts as the physical controller. Click **ACK all pending commands** within 8 seconds of each request. Then click **Recover after all-red ACK**. If the initialization commands time out, click **Retry all-red reconciliation**, ACK the replacement commands, and Recover. Timers begin after the final ACK, so leaving commands unconfirmed intentionally prevents progression.

```bash
python -m traffic                         # Manual REST controller simulation
python -m traffic --simulate --port 8080 # Alternate port
python -m traffic --db data/demo.sqlite3 # Separate persistent database
python -m unittest discover -s tests -v # Domain, persistence, concurrency and API tests
python scripts/demo.py                  # Automated end-to-end demonstration, isolated temporary DB
```

Stop with Ctrl+C. Restart with the same database path to preserve data. The default database is `data/traffic.sqlite3`, relative to the working directory. Run one server per database; an OS lifetime lock rejects a second instance. No credentials are needed. This is a local evaluation server, not an internet-facing deployment.

Optional Docker:

```bash
docker compose up --build
```

The Docker volume preserves the database. The image runs the auto-ACK simulator. Open the same dashboard and click Recover. Docker configuration is supplied; the Python application and tests were verified directly.

## What is implemented

- All six required API groups, plus device-status events and reconciliation commands.
- Safe GREEN → YELLOW → ALL_RED → GREEN transitions with confirmation barriers.
- Separate desired/confirmed signals, correlated ACKs, NACK handling and command deadlines.
- Queue tracking by vehicle, durable event-ID deduplication, sequence tombstones and validation.
- Weighted scheduling, aging, starvation protection, manual leases and emergency preemption.
- Conservative failure latching, offline sensor/signal/controller handling and explicit recovery.
- Durable state, pending commands, completed commands, emergency/manual state and audit history.
- Additional independently configured junctions using the same engine.
- Dashboard intersection, signal mismatch alerts, queues, controls, emergency display, controller outbox and recent history.
- REST simulation of arrivals, clearances, duplicates, failures and acknowledgements.
- Transactional processing of simultaneous requests and deterministic domain tests.

See [API reference](docs/API.md), [demo scenarios](docs/DEMO.md), [architecture](docs/ARCHITECTURE.md), and [verification notes](docs/VERIFICATION.md).

## Traffic-control algorithm

The two compatible movement groups are NORTH/SOUTH and EAST/WEST. Each phase's score is the sum of queued vehicle weights plus its longest effective waiting time divided by 5. Weights are employee=1, forklift=2, truck=4 and emergency=20. An active emergency uses the stronger preemption policy below instead of this score.

Effective waiting time starts at the later of vehicle arrival and the phase's last confirmed GREEN service. A waiting phase with at least 90 seconds of effective wait overrides ordinary scores. Among overdue phases, the longest wait wins. Ties favor the current phase, then the phase name. At each 30-second confirmed GREEN interval, reevaluate; retain the current phase if it remains preferred. Otherwise begin YELLOW. No demand eventually produces ALL_RED. Queue size matters through the sum of per-vehicle weights. Only explicit clearance events remove vehicles.

Manual requests and emergency priority can shorten the ordinary 30-second GREEN interval. They never shorten the 5-second confirmed YELLOW or 2-second confirmed ALL_RED clearance. A partially acknowledged signal batch must finish before preemption can proceed; a missing ACK faults within 8 seconds. Once YELLOW begins it completes even if the target changes. The target is recalculated before issuing the next GREEN.

Starvation protection bounds ordinary scheduling preference, not physical vehicle departure. It is suspended during manual/emergency priority, controller faults and unconfirmed transitions. Continuous emergency arrivals or repeatedly renewed manual requests can delay ordinary traffic; the assessment does not specify an acceptable emergency-vs-fairness tradeoff.

## Traffic-state transitions

| Current stage | Condition | Next operation |
| --- | --- | --- |
| ALL_RED | All lamps confirmed, clearance elapsed, demand exists | Request selected phase GREEN |
| GREEN | Confirmed timer elapsed and another phase/no demand wins | Request current phase YELLOW |
| GREEN | Conflicting manual/emergency priority | Request current phase YELLOW after pending ACK barrier |
| YELLOW | All lamps confirmed YELLOW/RED, 5 seconds elapsed | Request ALL_RED |
| Any | Offline device, NACK, mismatch, timeout or restart | Latch DEGRADED; request all RED; mark actual signals UNKNOWN |
| DEGRADED | Healthy devices, all-red confirmed, operator Recover | Start a fresh 2-second all-red clearance |

Each request creates four unique per-direction command IDs under a monotonically increasing junction epoch. The four ACKs form one barrier. No conflicting GREEN is sent until every prior RED is confirmed and clearance has elapsed. Timers are driven by a background 200 ms scheduler, with no blocking waits in request handlers. Late scheduler wakeups extend safety intervals instead of skipping stages.

## Assumptions / Questions / Requirement Issues

| Issue | Implemented decision |
| --- | --- |
| Conflicting movements and turns unspecified | Only straight NORTH/SOUTH and EAST/WEST pairs are compatible. No turning movements. Phase names and durations can vary by junction; incompatible direction groupings are rejected. |
| Sensor/sequence identity unspecified | `event_id` is globally unique across vehicle and device events. A matching repeated payload returns its original outcome without changing state; different payload reuse is HTTP 409. Sequence numbers are strictly increasing **per vehicle per junction**, including clearance tombstones. An actual device adapter must translate device-wide sequences or supply vehicle-scoped ordering. |
| Multiple delayed/reordered events | Sensor time is retained as evidence. Server receipt time controls queue aging and leases. Accept events at most 5 minutes old or 30 seconds ahead; reject older/future events. Reject stale per-vehicle sequences. A later clearance arriving first creates a tombstone, so an earlier arrival cannot resurrect the vehicle. No buffering/replay of stale detections. |
| Timestamp and sequence disagreement | Sequence controls ordering; timestamp controls freshness only. Device statuses use monotonically increasing sensor timestamps per device type/direction. Device clocks must be synchronized. |
| Rejected event retry | Well-formed IDs reserve the original result, including rejection. Correct a rejected payload using a new event ID. Invalid events lacking a usable ID are audited but cannot be deduplicated. |
| Clearance without arrival | Accept as a no-op, persist sequence tombstone and audit `UNMATCHED_CLEARANCE`. Wrong-direction clearance of an existing vehicle is rejected. Queues cannot become negative. |
| Duplicate logical arrival under a different event ID | Reject while that vehicle is already queued. Vehicle IDs must remain unique within a junction. Reusing an ID after clearance requires a higher sequence number. |
| Material-carrying vehicle absent from supported enum | Use the four explicit types in the specification. Classify material carriers as FORKLIFT or TRUCK upstream. Unknown types are rejected. |
| Manual duration/disconnection/races | Lease lasts 120 seconds from server receipt. Last committed manual request wins, including renewal. Disconnection does not extend the lease. Return-to-automatic cancels it. |
| Emergency overrides manual | Yes. Manual lease keeps aging and resumes after emergency if still valid. Multiple emergencies use FIFO server-arrival order, vehicle ID as deterministic tie-breaker. |
| Emergency completion and stale emergency | Explicit clearance ends priority. After 120 seconds, priority expires and is audited; the vehicle stays queued with its ordinary numeric weight until cleared. Repeated IDs do not renew priority. |
| ACK timeout, retries and duplicates | 8 seconds per batch. No automatic retries of GREEN/YELLOW. Fault requests a fresh fenced all-red batch. If that also times out, stop retrying; operator Reconcile is required. Identical completed ACKs are harmless; conflicting duplicates fault. Superseded ACKs never update current state. |
| Controller/signal/sensor offline | Conservatively freeze the junction in DEGRADED and request all RED. A failure of even one sensor prevents automatic scheduling. Queues still accept valid events. |
| Reconnect | ONLINE does not clear the fault. Issue fresh all-red commands, confirm them, then explicitly Recover. All reported failed devices must be restored individually. |
| Desired vs actual and physical safety | Sending RED does not prove RED. Unconfirmed physical state is UNKNOWN. Never grant a new GREEN while degraded or pending. Hardware must enforce interlocks, reject stale epochs/expired commands and stop safely on lost communication. Software cannot force a disconnected or stuck lamp to obey. |
| Restart during any stage | Persist queues, event receipts, leases, modes, signals, command IDs and history. Invalidate prior physical assumptions, supersede pending commands, increment epoch, request fresh all-red confirmation and require Recover. Old timers are not resumed. Recover clears the manual lease; unexpired emergency demand survives. |
| Wall clock changes | Server UTC governs persisted deadlines. Operation assumes a synchronized clock without abrupt jumps. A hardware controller must independently enforce minimum dwell/clearance and its watchdog. A production system should add clock-jump detection and monotonic runtime deadlines. |
| Auth and access policy | No authentication, as allowed by the assessment. Bind to loopback by default, no CORS access, and JSON-only write endpoints. Public deployment requires authentication, authorization, TLS and a production HTTP server. |
| Multiple service instances | One process owns each SQLite DB. SQLite `BEGIN IMMEDIATE` serializes every mutation; the process lock prevents another startup from resetting a live controller. Multi-host control is out of scope. |

## Major architectural decisions

1. **Pure domain engine.** `traffic/domain.py` accepts state and an injected timestamp; returns changed state and audit entries. It imports no HTTP, SQLite or device code. Tests advance virtual time without sleeps.
2. **Transactional application service.** `traffic/service.py` handles validation, durable receipts, application operations and the unit of work. SQLite serializes mutation across request and scheduler threads. Queue changes, outbox and audit entries commit together.
3. **SQLite JSON snapshots plus relational indexes.** State JSON captures the cohesive junction aggregate without partially updated lamps or queues. Receipts have a global primary key; audit rows have a junction/history index. `traffic/schema.sql` initializes schema version 1. This favors a small explainable submission; high-volume retention and analytics would use normalized tables and migrations.
4. **Durable outbox/controller port.** Pending commands live inside the transactionally stored state. The REST acknowledgement adapter and optional `SimulatedController` call the same application service. MQTT can consume the outbox and return ACKs without changing safety rules.
5. **Plain dashboard and polling.** One-second polling is sufficient for a human demonstration, while server scheduling runs every 200 ms. The browser renders authoritative state and sends intents only. It never computes traffic phases. API failures remain visible and do not erase the previous view.
6. **No dependencies.** The standard-library HTTP adapter keeps setup reproducible on Windows/macOS/Linux. It is appropriate for the local exercise, not a hardened production service.

## AI / Tool Usage

OpenAI ChatGPT/Codex was used to interpret the assessment, draft and implement the backend/frontend, write documentation and tests, and run automated verification. Python, SQLite, unittest, Node.js syntax checks were used during development. A Playwright browser test was prepared, but execution was blocked because a browser binary was unavailable and the browser download failed. This disclosure is included because the assessment explicitly permits AI assistance and requires its acknowledgement. The submitting candidate must review the code, run the scenarios and be able to explain or modify the solution. Do not describe this implementation as independently written without AI assistance.

## Scope and next work

All mandatory feature groups are implemented for a local REST simulation. Optional MQTT, WebSocket/SSE, production authentication, traffic analytics and audit replay are not implemented. Docker files are included but were not executed in this environment. No physical hardware was tested.

Before real deployment: validate controller firmware fencing/interlocks/watchdog behavior; add authenticated device identities and command authorization; replace the demo HTTP adapter; bound audit/receipt/completed-command growth; add structured migrations, monitoring and clock-jump handling; load-test realistic sensor rates. SQLite snapshots and unbounded retained event/command history are deliberately suitable for a small assessment, not indefinite factory operation.
