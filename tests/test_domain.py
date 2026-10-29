import random
import unittest
from copy import deepcopy
from traffic.domain import Engine, DEFAULT_CONFIG, DomainError


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine.create('A', DEFAULT_CONFIG, 1000)
        self.ack_all()
        self.e.command({'command': 'RECOVER'})
        self.e.now += 2
        self.e.tick()

    def ack_all(self):
        for c in list(self.e.s['pending_commands'].values()):
            self.e.ack({'command_id': c['command_id'], 'status': 'ACK', 'actual_state': c['requested_state']})
        self.e.tick()

    def arrival(self, vid, direction, kind='EMPLOYEE_VEHICLE', seq=1):
        self.e.sensor({'vehicle_id': vid, 'direction': direction, 'vehicle_type': kind,
                       'sequence_no': seq, 'event_type': 'VEHICLE_ARRIVED', 'timestamp': 'example'}, self.e.now)

    def advance(self, seconds):
        self.e.now += seconds
        self.e.tick()

    def green(self):
        self.arrival('n', 'NORTH')
        self.ack_all()
        self.assertEqual(self.e.s['stage'], 'GREEN')

    def assert_safe(self):
        for key in ('desired_signals', 'actual_signals'):
            signals = self.e.s[key]
            ns = any(signals[d] == 'GREEN' for d in ('NORTH', 'SOUTH'))
            ew = any(signals[d] == 'GREEN' for d in ('EAST', 'WEST'))
            self.assertFalse(ns and ew)

    def test_emergency_preemption_waits_for_yellow_and_clearance(self):
        self.green()
        self.arrival('ambulance', 'EAST', 'EMERGENCY')
        self.assertEqual(self.e.s['stage'], 'YELLOW')
        self.assertEqual(self.e.s['mode'], 'EMERGENCY')
        self.ack_all()
        self.advance(4.99)
        self.assertEqual(self.e.s['stage'], 'YELLOW')
        self.advance(.01)
        self.assertEqual(self.e.s['stage'], 'ALL_RED')
        self.ack_all()
        self.advance(1.99)
        self.assertEqual(self.e.s['stage'], 'ALL_RED')
        self.advance(.02)
        self.assertEqual(self.e.s['phase'], 'EAST_WEST')
        self.ack_all()
        self.assert_safe()

    def test_ack_barrier_starts_timer_only_after_final_ack(self):
        self.arrival('n', 'NORTH')
        cmds = list(self.e.s['pending_commands'].values())
        for c in cmds[:-1]:
            self.e.ack({'command_id': c['command_id'], 'status': 'ACK', 'actual_state': c['requested_state']})
        self.assertIsNone(self.e.s['deadline'])
        self.e.now += 3
        c = cmds[-1]
        self.e.ack({'command_id': c['command_id'], 'status': 'ACK', 'actual_state': c['requested_state']})
        self.assertEqual(self.e.s['deadline'], self.e.now + 30)

    def test_manual_emergency_precedence_and_expiry(self):
        self.green()
        self.e.command({'command': 'MANUAL_GREEN_REQUEST', 'direction': 'WEST'})
        self.arrival('ambulance', 'NORTH', 'EMERGENCY')
        self.assertEqual(self.e.choose(), ('EMERGENCY', 'NORTH_SOUTH'))
        self.e.sensor({'vehicle_id': 'ambulance', 'direction': 'NORTH', 'event_type': 'VEHICLE_CLEARED', 'sequence_no': 2, 'timestamp': 'example'}, self.e.now)
        self.assertEqual(self.e.choose(), ('MANUAL', 'EAST_WEST'))
        # Complete transitions while moving time, so no unrelated ACK timeout.
        self.ack_all()
        self.advance(5)
        self.ack_all()
        self.advance(2)
        self.ack_all()
        self.advance(114)
        self.assertIsNone(self.e.s['manual'])

    def test_priority_scoring_and_starvation(self):
        # Hold initial clearance while both queues arrive.
        self.e.s['deadline'] = self.e.now + 2
        self.arrival('e', 'EAST')
        self.arrival('n', 'NORTH', 'TRUCK')
        self.assertEqual(self.e.choose()[1], 'NORTH_SOUTH')
        self.advance(2)
        self.ack_all()
        for _ in range(3):
            self.advance(30)
            if self.e.s['stage'] == 'YELLOW':
                break
        self.assertEqual(self.e.s['stage'], 'YELLOW')
        self.assertEqual(self.e.choose()[1], 'EAST_WEST')

    def test_competing_emergencies_fifo_then_timeout(self):
        self.arrival('first', 'NORTH', 'EMERGENCY')
        self.ack_all()
        self.advance(1)
        self.arrival('second', 'EAST', 'EMERGENCY')
        self.assertEqual(self.e.choose()[1], 'NORTH_SOUTH')
        self.advance(119)
        self.assertTrue(self.e.s['vehicles']['first']['emergency_expired'])
        self.assertEqual(self.e.choose(), ('EMERGENCY', 'EAST_WEST'))

    def test_unknown_clear_tombstone_prevents_resurrection(self):
        self.e.sensor({'vehicle_id': 'x', 'direction': 'WEST', 'sequence_no': 4, 'event_type': 'VEHICLE_CLEARED', 'timestamp': 'example'}, self.e.now)
        with self.assertRaises(DomainError):
            self.arrival('x', 'WEST', seq=3)
        self.assertEqual(len(self.e.s['vehicles']), 0)

    def test_duplicate_vehicle_and_wrong_clear_direction_rejected(self):
        self.arrival('x', 'WEST')
        with self.assertRaises(DomainError):
            self.arrival('x', 'WEST', seq=2)
        with self.assertRaises(DomainError):
            self.e.sensor({'vehicle_id':'x','direction':'NORTH','sequence_no':2,'event_type':'VEHICLE_CLEARED','timestamp':'example'},self.e.now)
        self.assertEqual(len(self.e.s['vehicles']),1)

    def test_timeout_latches_and_requires_reconciliation(self):
        self.arrival('n', 'NORTH')
        self.advance(8)
        self.assertEqual(self.e.s['mode'], 'DEGRADED')
        self.assertEqual(set(self.e.s['desired_signals'].values()), {'RED'})
        self.advance(8)
        self.assertFalse(self.e.s['pending_commands'])
        with self.assertRaises(DomainError):
            self.e.command({'command': 'RECOVER'})
        self.e.command({'command':'RECONCILE'})
        self.ack_all()
        self.e.command({'command': 'RECOVER'})
        self.assertEqual(self.e.s['mode'], 'AUTOMATIC')

    def test_nack_mismatch_and_stale_ack(self):
        self.arrival('n','NORTH')
        cmds=list(self.e.s['pending_commands'].values())
        self.e.ack({'command_id':cmds[0]['command_id'],'status':'ACK','actual_state':'RED'})
        self.assertEqual(self.e.s['mode'],'DEGRADED')
        current=deepcopy(self.e.s['pending_commands'])
        self.e.ack({'command_id':cmds[1]['command_id'],'status':'ACK','actual_state':cmds[1]['requested_state']})
        self.assertEqual(self.e.s['pending_commands'],current)
        self.assertEqual(set(self.e.s['actual_signals'].values()),{'UNKNOWN'})

    def test_failure_and_restart_discard_physical_assumptions(self):
        self.green()
        self.e.restart()
        self.assertEqual(set(self.e.s['actual_signals'].values()),{'UNKNOWN'})
        self.assertEqual(set(self.e.s['desired_signals'].values()),{'RED'})
        self.assertIn('n',self.e.s['vehicles'])
        self.assertEqual(self.e.s['mode'],'DEGRADED')

    def test_sensor_failure_requires_online_and_red_confirmation(self):
        self.green()
        self.e.device({'device_type':'SENSOR','direction':'NORTH','status':'OFFLINE'},self.e.now)
        self.ack_all()
        with self.assertRaises(DomainError):
            self.e.command({'command':'RECOVER'})
        self.advance(1)
        self.e.device({'device_type':'SENSOR','direction':'NORTH','status':'ONLINE'},self.e.now)
        self.ack_all()
        self.e.command({'command':'RECOVER'})
        self.assertIsNone(self.e.s['fault'])

    def test_randomized_operations_preserve_signal_invariants(self):
        rng=random.Random(57)
        for i in range(1000):
            choice=rng.randrange(6)
            if choice==0 and not self.e.s['fault']:
                self.arrival(str(i),rng.choice(['NORTH','SOUTH','EAST','WEST']),rng.choice(['TRUCK','FORKLIFT','EMERGENCY']))
            elif choice==1:
                self.ack_all()
            elif choice==2:
                self.advance(rng.uniform(0,4))
            elif choice==3 and not self.e.s['fault']:
                self.e.command({'command':'MANUAL_GREEN_REQUEST','direction':rng.choice(['NORTH','WEST'])})
            elif choice==4:
                self.e.command({'command':'RETURN_TO_AUTOMATIC'})
            elif choice==5 and self.e.s['fault']:
                self.e.command({'command':'RECONCILE'})
                self.ack_all()
                self.e.command({'command':'RECOVER'})
            self.assert_safe()
