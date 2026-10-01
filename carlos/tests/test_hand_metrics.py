import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from ev.hand_metrics import distribution, sample, summarize


def row(at,captured,inferred,skipped=0,pid=40,start=20,state='READY'):
    return {'time':at,'status':{'available':True,'peer_pid':pid,'peer_start_ticks':start,
        'state':state,'counters':{'captured':captured,'inferred':inferred,'skipped':skipped},
        'tracking':{'inference_ms':12,'age_ms':30,'hand_visible':False}}}


class HandMetricsTests(unittest.IsolatedAsyncioTestCase):
    def test_rates_use_counter_deltas_and_real_observation_span(self):
        result=summarize([row(1,100,50,50),row(3,160,80,80)])
        self.assertEqual(result['capture_fps'],30)
        self.assertEqual(result['inference_fps'],15)
        self.assertEqual(result['counter_deltas']['skipped'],30)
        self.assertTrue(result['peer_continuity_verified'])
        self.assertIsNone(result['physical_gesture_latency_ms'])
        self.assertFalse(result['gesture_accuracy_verified'])
        self.assertEqual(result['frames_saved'],0)

    def test_reused_pid_or_reset_counter_invalidates_rates(self):
        for changed in (row(2,130,60,pid=41),row(2,130,60,start=21),row(2,90,60),row(2,130,60,pid=None)):
            result=summarize([row(1,100,50),changed])
            self.assertIsNone(result['capture_fps'])
            self.assertFalse(result['peer_continuity_verified'])

    def test_missing_counters_and_nonadvancing_clock_are_unavailable(self):
        missing=row(2,0,0);missing['status']['counters']={}
        for rows in ([row(1,100,50),missing],[row(1,100,50),row(1,130,60)],[]):
            self.assertIsNone(summarize(rows)['capture_fps'])
        self.assertIsNone(summarize([row(1,0,0),row(float('nan'),1,1)])['capture_fps'])

    def test_paused_zero_is_a_real_zero_and_failed_reads_are_visible(self):
        result=summarize([row(1,100,50,state='PAUSED'),row(3,100,50,state='PAUSED'),{'time':4,'status':{'available':False}}])
        self.assertEqual(result['capture_fps'],0)
        self.assertEqual(result['failed_observations'],1)
        self.assertEqual(result['states'],['PAUSED'])
        self.assertEqual(result['elapsed_seconds'],2)

    def test_sampled_distribution_does_not_claim_per_frame_latency(self):
        self.assertEqual(distribution([1,2,3,100])['p95'],100)
        self.assertEqual(distribution([1,2,3,100])['p50'],2.5)
        result=summarize([row(1,1,1),row(2,2,2)])
        self.assertEqual(result['sampled_inference_ms']['samples'],2)
        self.assertIn('repeat or miss',result['scope'])
        self.assertIsNone(result['desktop_action_latency_ms'])

    def test_idle_timings_are_excluded_but_zero_rates_remain_measured(self):
        rows = [row(1,100,50), row(3,100,50)]
        for item in rows:
            item['status']['pipeline_demand'] = 'IDLE'
        result = summarize(rows)
        self.assertEqual(result['pipeline_demands'], ['IDLE'])
        self.assertEqual(result['capture_fps'], 0)
        self.assertIsNone(result['sampled_inference_ms'])
        self.assertIsNone(result['sampled_age_ms'])

    async def test_missing_app_is_not_launched_and_returns_no_rates(self):
        status=AsyncMock(return_value={'available':False,'camera_started':False})
        with patch('ev.tools.holohand.status',status):
            result=await sample(1)
        status.assert_awaited_once_with({},None)
        self.assertIsNone(result['capture_fps'])
        self.assertFalse(result['camera_started'])

    async def test_cancel_stops_polling_without_control_commands(self):
        entered=asyncio.Event()
        async def blocked(*args):
            entered.set();await asyncio.Event().wait()
        status=AsyncMock(side_effect=blocked)
        with patch('ev.tools.holohand.status',status):
            task=asyncio.create_task(sample(1));await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        status.assert_awaited_once_with({},None)

    async def test_invalid_duration_is_refused_before_ipc(self):
        for value in (True,0,31,float('nan'),float('inf'),'5'):
            with patch('ev.tools.holohand.status',AsyncMock()) as status:
                with self.assertRaises(ValueError):await sample(value)
                status.assert_not_awaited()
