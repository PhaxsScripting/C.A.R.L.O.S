import asyncio
import unittest
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from ev.events import PhaxEventBus
from ev.telemetry import TelemetrySampler


class ThermalPriorityTests(unittest.TestCase):
    def setUp(self):
        self.bus = PhaxEventBus()
        self.sampler = TelemetrySampler(self.bus, config={'warning_repeat_seconds':120})

    def warnings(self):
        return [event for event in self.bus.history() if event['type']=='system.warning']

    def test_first_early_boot_warning_is_not_a_repeat(self):
        self.sampler.observe_thermal(91, now=1)
        self.sampler.observe_thermal(92, now=2)
        self.assertEqual(len(self.warnings()), 1)
        self.assertEqual(self.warnings()[0]['priority'], 'HIGH')

    def test_emergency_escalation_is_not_hidden_by_a_high_warning(self):
        self.sampler.observe_thermal(91, now=10)
        self.sampler.observe_thermal(99, now=11)
        self.sampler.observe_thermal(100, now=12)
        self.assertEqual([e['priority'] for e in self.warnings()], ['HIGH','EMERGENCY'])
        self.assertEqual(self.warnings()[1]['payload']['celsius'], 99)

    def test_each_priority_keeps_its_repeat_budget_through_flapping(self):
        for temperature,now in [(91,10),(99,11),(97,12),(99,13),(91,130),(99,131)]:
            self.sampler.observe_thermal(temperature,now=now)
        self.assertEqual([e['priority'] for e in self.warnings()], ['HIGH','EMERGENCY','HIGH','EMERGENCY'])
        self.assertEqual(len(self.sampler._thermal_warning_times),2)

    def test_invalid_readings_cannot_consume_warning_budgets(self):
        for value in [None, True, '99', float('nan'),float('inf'),float('-inf'),10**400]:
            self.sampler.observe_thermal(value,now=1)
        self.assertEqual(self.warnings(),[])
        self.assertEqual(self.sampler._thermal_warning_times,{})
        self.sampler.observe_thermal(99,now=2)
        self.assertEqual(self.warnings()[0]['priority'],'EMERGENCY')

    def test_normal_temperature_never_claims_a_warning(self):
        self.sampler.observe_thermal(60,now=1)
        self.assertEqual(self.warnings(),[])
        self.assertIsNone(self.sampler._last_thermal_warning)

    def test_invalid_config_uses_finite_defaults_without_changing_config(self):
        for value in ['bad',None,True,False,float('nan'),float('inf'),10**400]:
            self.sampler=TelemetrySampler(self.bus,config={'warning_repeat_seconds':value,'warning_temperature_celsius':value})
            self.sampler.observe_thermal(91,now=1)
            self.sampler.observe_thermal(92,now=2)
            self.assertEqual(len(self.sampler._thermal_warning_times),1)
            self.assertIs(self.sampler.config['warning_repeat_seconds'],value)


class ThermalLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_core_retains_escalation_and_answers_ipc_behind_a_held_notice(self):
        fixture=Path(__file__).with_name('fixtures')/'thermal_escalation_live.py'
        result=await asyncio.to_thread(subprocess.run,[sys.executable,str(fixture)],
                                      capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['warning_priorities'],['HIGH','EMERGENCY'])
        self.assertTrue(report['emergency_delivered_first_after_release'])
        self.assertEqual(report['actions_executed'],0)
        self.assertFalse(report['real_thermal_stress'])

    async def test_real_telemetry_loop_delivers_escalation_while_cooldown_is_active(self):
        bus=PhaxEventBus()
        sampler=TelemetrySampler(bus,config={'warning_repeat_seconds':120})
        sampler.interval=.005
        stop=asyncio.Event()
        readings=iter([91,99,99,99,99])
        base={'resource_mode':'NORMAL','memory':{'available_bytes':1000000},
              'network':{'link_state':'UP'},'battery':None}
        def sample():
            return {**base,'cpu_temperature':{'celsius':next(readings,99),'sensor':'disposable-fixture'}}
        with patch.object(sampler,'sample',side_effect=sample):
            task=asyncio.create_task(sampler.run(stop))
            try:
                async with asyncio.timeout(2):
                    while len([e for e in bus.history() if e['type']=='system.warning'])<2:
                        await asyncio.sleep(.005)
            finally:
                stop.set()
                await task
        warnings=[e for e in bus.history() if e['type']=='system.warning']
        self.assertEqual([e['priority'] for e in warnings],['HIGH','EMERGENCY'])
        self.assertEqual(len([e for e in bus.history() if e['type']=='system.error']),0)
        self.assertGreaterEqual(len([e for e in bus.history() if e['type']=='system.telemetry']),2)


if __name__=='__main__':
    unittest.main()
