# Verification record

Verified in the development environment with Python 3 and SQLite. These commands are reproducible from the project root.

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 22 tests passed |
| `python scripts/demo.py` | All six end-to-end scenario groups passed using a real HTTP subprocess and temporary SQLite DB |
| `python -m compileall -q traffic` | Passed |
| `node --check traffic/static/app.js` | Passed |
| HTTP dashboard HTML and JavaScript serving | Passed in API test |
| Browser visual / interaction test | Not executed successfully: Playwright package available, but no Chromium binary; download failed |
| Docker build/run | Not executed in this environment |
| Physical controller / MQTT | Not implemented or tested; REST simulation is the supported assessment option |

The suite checks confirmed timing barriers, emergency preemption, manual/emergency precedence, emergency expiry, weighted scheduling and starvation, clearance tombstones, duplicate queue protection, wrong-direction clearance, controller timeouts, mismatch handling, stale ACKs, restart reconciliation, sensor failure recovery, 1,000 randomized domain operations, durable receipts, rejection auditing, 150 concurrent arrivals/duplicates, mixed concurrent emergency/manual/ACK processing, delayed events, multiple junctions, configuration safety, controller event replay, conflicting ACKs and HTTP validation.

Browser test source is supplied for an evaluator with Node.js:

```bash
# Terminal 1: use a NEW database so the test starts from initial state
python -m traffic --simulate --port 8765 --db data/browser-check.sqlite3

# Terminal 2: optional development-only dependencies
npm install --no-save playwright
npx playwright install chromium
node scripts/browser-check.cjs
```

The browser script checks startup recovery, arrival, duplicate handling, emergency display and transition, clearance, absence of JavaScript exceptions and mobile horizontal overflow. It assumes a fresh junction and is supplied for further validation; its assertions were not claimed as passing during development.

## Requirement coverage

| Assessment sections | Implementation |
| --- | --- |
| 2–3 Junction phases and signal safety | domain.py + validated junction config |
| 4–5 Detection and queues | Service.event, receipt table, Engine.sensor |
| 6 Automatic control and concurrency | Engine.choose/tick, SQLite transaction serialization |
| 7 Emergency handling | FIFO active emergencies, safe preemption, explicit clear/expiry |
| 8 Manual controls | Timed manual lease, return-to-automatic intent |
| 9 Failures and ACKs | Engine.ack/fail/device, reconciliation and recovery |
| 10 APIs | server.py; docs/API.md |
| 11 Audit history | Transactional append-only history table and dashboard |
| 12 Persistence/restart | Snapshots, receipts, command fencing and boot reconciliation |
| 13 IoT | REST ACK adapter, simulated ControllerPort implementation |
| 14 Frontend | static/index.html, app.js, style.css |
| 15 Scenarios | docs/DEMO.md, scripts/demo.py and tests |
| 16 Engineering expectations | Pure engine, validation, transactional service and tests |
| 18–19 Submission docs | README, schema.sql, API/architecture/demo docs, tests and AI disclosure |

No credentials, generated runtime database, browser binaries, caches or third-party packages are included in the submission archive.
