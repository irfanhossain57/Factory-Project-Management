"""Controller port and explicitly simulated adapter. Never used by the domain."""
from typing import Protocol


class ControllerPort(Protocol):
    def poll(self, service): ...


class SimulatedController:
    """Consumes durable outbox commands and submits the same ACKs as REST devices."""
    def poll(self, service):
        for state in service.list():
            if state['controller_status'] != 'ONLINE':
                continue
            for cmd in state['pending_commands'].values():
                service.ack({'junction_id': state['junction_id'], 'command_id': cmd['command_id'],
                             'status': 'ACK', 'actual_state': cmd['requested_state']})
