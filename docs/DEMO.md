# Evaluator demonstration

Start `python -m traffic --simulate`, open http://127.0.0.1:8000, wait for all actual lamps RED and click Recover. Use the dashboard for the following scenarios. The main mode/phase cards and history expose every decision.

1. **Normal traffic:** Add NORTH employee vehicle and EAST employee vehicle. NORTH receives GREEN first if submitted first after clearance. Observe 30-second service intervals and safe YELLOW/all-red transitions when the other phase wins. Clear vehicles explicitly.
2. **Priority:** In a fresh/recovered all-red junction, add EAST employee and NORTH truck before the 2-second clearance ends. NORTH's score is greater. Alternatively use domain test `test_priority_scoring_and_starvation` for deterministic simultaneous demand. A truck does not immediately preempt an already GREEN phase.
3. **Emergency:** While NORTH/SOUTH is confirmed GREEN, add an EAST EMERGENCY. Mode becomes EMERGENCY, NORTH/SOUTH requests YELLOW, waits 5 seconds after confirmation, requests ALL_RED, waits 2 seconds, then EAST/WEST requests GREEN. Clear the emergency to end its priority. Conflicting emergencies use oldest first.
4. **Manual:** Select WEST and Request manual green. The same safe transition runs. Return to automatic cancels the lease. A manual lease expires after 120 seconds and emergency demand overrides it.
5. **Duplicate:** Add a vehicle and click Repeat last event. The count remains unchanged. Open the audit detail to see DUPLICATE_SENSOR_EVENT.
6. **Clearance:** Click Clear beside an existing vehicle. Its queue count decreases by one. Repeating that exact event cannot decrement again. An unknown clearance creates a sequence tombstone but no negative count.
7. **Device failure:** Report SIGNAL_CONTROLLER OFFLINE. Mode becomes DEGRADED; actual states become UNKNOWN; desired lamps become RED. Report ONLINE using the same direction. With the simulator, new red commands are ACKed. Click Recover. Repeat with SENSOR or SIGNAL; each failed key must report ONLINE before recovery.
8. **Missing ACK:** Restart without `--simulate`. Do not ACK the startup batch. After 8 seconds the command timeout appears; state remains DEGRADED. Click Retry all-red reconciliation, then ACK all within 8 seconds and Recover. Start a manual GREEN request and withhold ACK to exercise failure from normal operation. NACK any pending command for immediate fault simulation.
9. **Restart:** Add vehicles and establish a manual or emergency state. Stop and restart the process with the same DB. Queues/receipts/history survive, but actual lamps start UNKNOWN. Fresh all-red ACKs and Recover are mandatory. Old command IDs cannot authorize a new phase. `test_restart_pending_commands_fenced` tests the specific pending-command case.
10. **Concurrent events:** Run `python -m unittest discover -s tests -v`. The service test submits 150 concurrent requests representing 50 arrivals and two duplicates of each. Exactly 50 entries remain. Mixed emergency/manual/ACK ordering is covered by a seeded 1,000-step domain test and the mixed concurrency test.
11. **Second junction:** POST `{"junction_id":"B"}` to `/api/junctions`. It appears in the dashboard selector on the next poll. ACK/recover B independently. Its queues and configuration do not affect A.

## Automated demonstration

`python scripts/demo.py` starts its own server on a free loopback port with a fresh temporary database. It checks health, startup recovery, arrival and duplicate handling, GREEN confirmation, emergency transition, clearance, manual intent, return to automatic, failure/reconnect and restart recovery. It prints each completed step and removes its temporary database when finished. It does not alter `data/traffic.sqlite3`.

## Suggested review preparation

Read Engine.request, Engine.ack and Engine.tick together; explain the four-ACK barrier and why timers begin on confirmation. Then explain Service.event's transaction and receipt table. Walk through a timeout and restart. Be ready to modify manual lease duration or vehicle weights, add a test, and explain the real hardware controller contract in ARCHITECTURE.md.
