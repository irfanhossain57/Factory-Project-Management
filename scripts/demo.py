"""Run the real HTTP application against a disposable persistent database."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    process = None
    with tempfile.TemporaryDirectory() as tmp:
        database = str(Path(tmp) / 'demo.sqlite3')
        def launch():
            return subprocess.Popen([sys.executable, '-m', 'traffic', '--simulate', '--port', str(port), '--db', database], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def api(path, data=None):
            req = Request(base+path, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type':'application/json'})
            with urlopen(req, timeout=3) as response:
                return json.load(response)
        def wait(check, timeout=15):
            end = time.monotonic()+timeout
            last = None
            while time.monotonic()<end:
                try:
                    last = check()
                    if last:
                        return last
                except OSError:
                    pass
                time.sleep(.1)
            raise AssertionError(f'Condition not reached within {timeout}s; last value: {last}')
        def status():
            return api('/api/junctions/A/status')
        def command(name, **extra):
            return api('/api/junctions/A/commands', {'command':name, **extra})
        def sensor(vid, direction, kind='TRUCK', clear=False):
            return {'event_id':f'{vid}-{time.time_ns()}','junction_id':'A','direction':direction,'vehicle_id':vid,
                    'vehicle_type':kind,'event_type':'VEHICLE_CLEARED' if clear else 'VEHICLE_ARRIVED',
                    'sequence_no':2 if clear else 1,'timestamp':datetime.now(timezone.utc).isoformat()}
        def stop():
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        try:
            process=launch()
            wait(lambda:api('/health')['status']=='ok')
            wait(lambda:not status()['pending_commands'])
            command('RECOVER')
            print('PASS: startup all-red confirmation and recovery', flush=True)
            arrival=sensor('truck','NORTH')
            api('/api/sensor-events',arrival)
            assert api('/api/sensor-events',arrival)['duplicate']
            assert status()['queues']['NORTH']==1
            wait(lambda:status()['actual_signals']['NORTH']=='GREEN')
            print('PASS: arrival, duplicate and confirmed GREEN', flush=True)
            api('/api/sensor-events',sensor('ambulance','EAST','EMERGENCY'))
            wait(lambda:status()['stage']=='YELLOW')
            wait(lambda:status()['actual_signals']['EAST']=='GREEN')
            assert status()['actual_signals']['NORTH']=='RED'
            print('PASS: emergency preemption through YELLOW and all-red', flush=True)
            api('/api/sensor-events',sensor('ambulance','EAST','EMERGENCY',clear=True))
            command('MANUAL_GREEN_REQUEST',direction='WEST')
            assert status()['mode']=='MANUAL'
            command('RETURN_TO_AUTOMATIC')
            print('PASS: clearance, manual override and automatic return', flush=True)
            for device_status in ('OFFLINE','ONLINE'):
                api('/api/device-events',{'event_id':f'device-{device_status}','junction_id':'A','device_type':'SIGNAL_CONTROLLER','status':device_status,'timestamp':datetime.now(timezone.utc).isoformat()})
                if device_status=='OFFLINE':
                    assert status()['mode']=='DEGRADED'
                    assert set(status()['actual_signals'].values())=={'UNKNOWN'}
            wait(lambda:not status()['pending_commands'])
            command('RECOVER')
            print('PASS: controller failure, reconnect and safe recovery', flush=True)
            stop()
            process=launch()
            wait(lambda:api('/health')['status']=='ok')
            assert status()['queues']['NORTH']==1
            assert status()['mode']=='DEGRADED'
            assert api('/api/sensor-events',arrival)['duplicate']
            wait(lambda:not status()['pending_commands'])
            command('RECOVER')
            print('PASS: restart persistence, durable deduplication and reconciliation', flush=True)
            print('All end-to-end scenarios passed.', flush=True)
        finally:
            stop()

if __name__=='__main__':
    main()
