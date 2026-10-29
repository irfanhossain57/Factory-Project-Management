import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from traffic.domain import DomainError
from traffic.repository import Repository
from traffic.service import Service
from traffic.server import handler_for
from traffic.controller import SimulatedController


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now=1800000000.
        self.s=Service(Repository(Path(self.tmp.name)/'db.sqlite3'),lambda:self.now)
        self.s.boot()

    def event(self, eid='one', vid='v1', direction='NORTH', seq=1):
        return {'event_id':eid,'junction_id':'A','direction':direction,'vehicle_id':vid,'vehicle_type':'TRUCK',
                'event_type':'VEHICLE_ARRIVED','sequence_no':seq,'timestamp':datetime.fromtimestamp(self.now,timezone.utc).isoformat()}

    def test_duplicates_conflicts_and_durable_receipts(self):
        event=self.event()
        self.assertEqual(self.s.event(event)[1],200)
        self.assertTrue(self.s.event(event)[0]['duplicate'])
        self.assertEqual(self.s.status('A')['queues']['NORTH'],1)
        self.assertEqual(self.s.event({**event,'direction':'EAST'})[1],409)
        self.s.boot()
        self.assertTrue(self.s.event(event)[0]['duplicate'])
        self.assertEqual(self.s.status('A')['queues']['NORTH'],1)
        self.assertTrue(any(h['event_type']=='RESTART_RECOVERY' for h in self.s.history('A')))

    def test_rejected_events_are_audited_and_deduplicated(self):
        bad={**self.event(),'vehicle_type':'SPACESHIP'}
        self.assertEqual(self.s.event(bad)[1],422)
        self.assertTrue(self.s.event(bad)[0]['duplicate'])
        self.assertEqual(self.s.status('A')['queues']['NORTH'],0)
        self.assertTrue(any(h['event_type']=='REJECTED_SENSOR_EVENT' for h in self.s.history('A')))

    def test_concurrent_events_and_duplicates(self):
        events=[self.event(str(i),str(i)) for i in range(50)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(self.s.event,events*3))
        self.assertTrue(all(code==200 for _,code in results))
        self.assertEqual(self.s.status('A')['queues']['NORTH'],50)
        self.assertEqual(sum(not result['duplicate'] for result,_ in results),50)

    def test_delayed_event_and_sequence_scope(self):
        self.assertEqual(self.s.event(self.event(seq=10))[1],200)
        self.assertEqual(self.s.event(self.event('old',seq=9))[1],409)
        self.assertEqual(self.s.event(self.event('other','other',seq=1))[1],200)
        self.assertEqual(self.s.event({**self.event('late','late'),'timestamp':'2020-01-01T00:00:00Z'})[1],422)

    def test_multi_junction_isolation_and_config(self):
        self.s.create({'junction_id':'B','config':{'green_seconds':40}})
        self.s.event({**self.event(),'junction_id':'B'})
        self.assertEqual(self.s.status('A')['queues']['NORTH'],0)
        self.assertEqual(self.s.status('B')['queues']['NORTH'],1)
        self.assertEqual(self.s.status('B')['config']['green_seconds'],40)
        with self.assertRaises(DomainError):
            self.s.create({'junction_id':'C','config':{'yellow_seconds':1}})
        with self.assertRaises(DomainError):
            self.s.create({'junction_id':'A'})

    def test_restart_pending_commands_fenced(self):
        old=list(self.s.status('A')['pending_commands'].values())[0]
        old_epoch=old['epoch']
        self.s.boot()
        self.s.ack({'junction_id':'A','command_id':old['command_id'],'status':'ACK','actual_state':'RED'})
        st=self.s.status('A')
        self.assertGreater(st['epoch'],old_epoch)
        self.assertEqual(set(st['actual_signals'].values()),{'UNKNOWN'})
        SimulatedController().poll(self.s)
        self.s.command('A',{'command':'RECOVER'})
        self.assertEqual(self.s.status('A')['mode'],'AUTOMATIC')

    def test_device_event_replay_and_stale_status(self):
        event={'event_id':'d1','junction_id':'A','device_type':'SENSOR','direction':'EAST','status':'OFFLINE','timestamp':self.event()['timestamp']}
        self.assertEqual(self.s.event(event,True)[1],200)
        self.assertTrue(self.s.event(event,True)[0]['duplicate'])
        self.assertTrue(all(isinstance(h['timestamp'], (int, float)) for h in self.s.history('A')))
        self.assertEqual(self.s.event({**event,'event_id':'d2','status':'ONLINE'},True)[1],409)

    def test_conflicting_ack_duplicate_fails_closed(self):
        cmd=list(self.s.status('A')['pending_commands'].values())[0]
        data={'junction_id':'A','command_id':cmd['command_id'],'status':'ACK','actual_state':'RED'}
        self.s.ack(data)
        self.s.ack(data)
        st=self.s.ack({**data,'actual_state':'GREEN'})
        self.assertEqual(st['mode'],'DEGRADED')
        self.assertIn('Conflicting',st['fault'])

    def test_mixed_concurrent_emergency_manual_duplicate_and_ack(self):
        SimulatedController().poll(self.s)
        self.s.command('A',{'command':'RECOVER'})
        self.now += 2
        self.s.event(self.event())
        SimulatedController().poll(self.s)
        emergency={**self.event('emergency','ambulance','EAST'), 'vehicle_type':'EMERGENCY'}
        def do(i):
            if i in (0,1): return self.s.event(emergency)
            if i==2: return self.s.command('A',{'command':'MANUAL_GREEN_REQUEST','direction':'WEST'})
            return SimulatedController().poll(self.s)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(do,range(4)))
        st=self.s.status('A')
        self.assertEqual(st['mode'],'EMERGENCY')
        self.assertEqual(st['queues']['EAST'],1)
        self.assertEqual(st['queues']['NORTH'],1)
        for key in ('desired_signals','actual_signals'):
            self.assertFalse(st[key]['NORTH']=='GREEN' and st[key]['EAST']=='GREEN')

    def test_http_routes_validation_and_dashboard(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.s))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path, data=None):
            req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
            try:
                with urlopen(req) as r:return r.status,r.read()
            except HTTPError as r:return r.code,r.read()
        self.assertEqual(request('/')[0],200)
        self.assertEqual(request('/app.js')[0],200)
        self.assertEqual(request('/api/junctions')[0],200)
        self.assertEqual(request('/api/junctions',{'junction_id':'B'})[0],201)
        self.assertEqual(request('/api/junctions/A/status')[0],200)
        self.assertEqual(request('/api/junctions/nope')[0],404)
        self.assertEqual(request('/api/sensor-events',self.event())[0],200)
        self.assertEqual(request('/api/sensor-events',[])[0],422)
        self.assertEqual(request('/api/sensor-events',{**self.event('weird'),'vehicle_type':[]})[0],422)
        self.assertEqual(request('/api/junctions/A/commands',{'command':'FORCE_GREEN'})[0],422)
        self.assertEqual(request('/api/junctions/A/history?limit=999')[0],422)
        self.assertEqual(request('/api/controller-events',{'junction_id':'A','command_id':'unknown','status':'ACK','actual_state':'RED'})[0],404)
        self.assertEqual(request('/api/device-events',{'event_id':'bad-device','junction_id':'A','device_type':'SIGNAL_CONTROLLER','direction':None,'status':'ONLINE','timestamp':self.event()['timestamp']})[0],422)

if __name__=='__main__':unittest.main()
