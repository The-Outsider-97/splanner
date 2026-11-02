"""Run: python -m unittest -v splanner_alarm_lifecycle_tests.py"""
import unittest
from datetime import datetime, timedelta, timezone
from applications.splanner.alarm_lifecycle import advance, dismiss, manual_snooze, reset

UTC = timezone.utc
T0 = datetime(2026, 11, 2, 9, tzinfo=UTC)
ISO = lambda t: t.strftime('%Y-%m-%dT%H:%M:%S+00:00')


def fresh(due=T0):
    return dict(id='a', status='pending', due_at=ISO(due), original_due_at=ISO(due),
                alarm_state='scheduled', ring_started_at=None, snooze_count=0,
                alarm_revision=0, last_transition_at=None, last_snooze_kind=None)


class AlarmLifecycleTest(unittest.TestCase):
    def test_normal_cycle(self):
        item = fresh()
        item.update(advance(item, T0))
        self.assertEqual(item['alarm_state'], 'ringing')
        self.assertEqual(item['ring_started_at'], ISO(T0))
        self.assertEqual(advance(item, T0 + timedelta(seconds=119)), {})
        item.update(advance(item, T0 + timedelta(seconds=120)))
        self.assertEqual((item['alarm_state'], item['due_at'], item['snooze_count']), ('snoozed', ISO(T0 + timedelta(minutes=7)), 1))
        self.assertEqual(advance(item, T0 + timedelta(minutes=6)), {})
        item.update(advance(item, T0 + timedelta(minutes=7)))
        self.assertEqual((item['alarm_state'], item['ring_started_at']), ('ringing', ISO(T0 + timedelta(minutes=7))))
        item.update(advance(item, T0 + timedelta(minutes=9)))
        self.assertEqual((item['alarm_state'], item['due_at'], item['snooze_count']), ('snoozed', ISO(T0 + timedelta(minutes=14)), 2))

    def test_idempotence_and_revision(self):
        item = fresh()
        item.update(advance(item, T0))
        self.assertEqual(advance(item, T0), {})
        self.assertEqual(item['alarm_revision'], 1)
        item.update(advance(item, T0 + timedelta(seconds=120)))
        self.assertEqual(item['alarm_revision'], 2)
        self.assertEqual(advance(item, T0 + timedelta(seconds=120)), {})

    def test_catches_up_after_sleep(self):
        item = fresh()
        item.update(advance(item, T0 + timedelta(minutes=29)))
        self.assertEqual(item['alarm_state'], 'ringing')
        self.assertEqual(item['ring_started_at'], ISO(T0 + timedelta(minutes=28)))
        self.assertEqual(item['snooze_count'], 4)

    def test_manual_snooze_and_dismiss(self):
        item = fresh()
        item.update(advance(item, T0))
        item.update(manual_snooze(item, T0 + timedelta(seconds=30), 10))
        self.assertEqual((item['alarm_state'], item['due_at']), ('snoozed', ISO(T0 + timedelta(minutes=10, seconds=30))))
        self.assertEqual(advance(item, T0 + timedelta(minutes=10)), {})
        item.update(dismiss(item, T0 + timedelta(minutes=11)))
        self.assertEqual(advance(item, T0 + timedelta(days=30)), {})
        self.assertEqual(item['alarm_state'], 'dismissed')

    def test_new_occurrence(self):
        item = fresh()
        item.update(dismiss(item, T0))
        item.update(reset(item, ISO(T0 + timedelta(days=1)), T0))
        self.assertEqual(item['alarm_state'], 'scheduled')
        self.assertEqual(item['snooze_count'], 0)

    def test_several_alarms_are_independent(self):
        a, b = fresh(), fresh(T0 + timedelta(minutes=3))
        a.update(advance(a, T0 + timedelta(seconds=121)))
        self.assertEqual(a['alarm_state'], 'snoozed')
        self.assertEqual(advance(b, T0 + timedelta(seconds=121)), {})
        b.update(advance(b, T0 + timedelta(minutes=3)))
        self.assertEqual(b['alarm_state'], 'ringing')

if __name__ == '__main__': unittest.main()
