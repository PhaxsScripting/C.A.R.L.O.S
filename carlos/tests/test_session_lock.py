import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from jeepney.low_level import HeaderFields
from ev.session_lock import SessionLockMonitor, SERVICES


def signal_message(service, path, owner, active, member='ActiveChanged'):
    return SimpleNamespace(header=SimpleNamespace(fields={HeaderFields.sender:owner,
        HeaderFields.interface:service, HeaderFields.member:member, HeaderFields.path:path}), body=(active,))


class SessionLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_signals_from_other_owner_path_and_interface(self):
        observed=[]
        m=SessionLockMonitor(lambda *row:observed.append(row))
        m.selected=SERVICES[0];m.owner=':1.4'
        service,path=m.selected
        for message in [signal_message(service,path,':1.5',True),
                        signal_message(service,'/wrong',m.owner,True),
                        signal_message('org.other',path,m.owner,True)]:
            await m.receive(message)
        self.assertEqual(observed,[])
        await m.receive(signal_message(service,path,m.owner,True))
        self.assertEqual(observed,[(True,None,service)])

    async def test_invalid_signal_clears_previously_unlocked_state(self):
        observed=[]
        m=SessionLockMonitor(lambda *row:observed.append(row));m.selected=SERVICES[0];m.owner=':1.4'
        await m.receive(signal_message(*m.selected,m.owner,'false'))
        self.assertEqual(observed,[(None,None,None)])
        self.assertIsNone(m.owner)

    async def test_unsupported_idle_method_does_not_disable_lock_or_get_retried(self):
        m=SessionLockMonitor(lambda *row:None);m.selected=SERVICES[0];m.owner=':1.4';m.locked=False
        m.call=AsyncMock(side_effect=RuntimeError('UnknownMethod'))
        await m.read_idle();await m.read_idle()
        self.assertEqual(m.call.await_count,1)
        self.assertFalse(m.idle_supported)
        self.assertIs(m.locked,False)

    async def test_stalled_method_has_a_deadline(self):
        m=SessionLockMonitor(lambda *row:None);m.probe_timeout=.02
        m.router=SimpleNamespace(send_and_get_reply=AsyncMock(side_effect=lambda _:asyncio.sleep(10)))
        async def stalled(_):await asyncio.sleep(10)
        m.router.send_and_get_reply=stalled
        with self.assertRaises(TimeoutError):await asyncio.wait_for(m.call(None),.2)

    def test_lost_service_clears_presence_and_cannot_fabricate_a_greeting(self):
        from ev.events import PhaxEventBus
        from ev.presence import PresenceMonitor
        bus=PhaxEventBus();p=PresenceMonitor(bus)
        p.observe_lock(True,100)
        p.observe_session(None,None,None)
        self.assertEqual(p.state['presence'],'UNKNOWN')
        p.observe_lock(False,1000)
        self.assertFalse(any(e['type']=='presence.returned' for e in bus.history()))

    @unittest.skipUnless(shutil.which('dbus-run-session'), 'Isolated D-Bus unavailable')
    def test_real_session_bus_desktops_replacement_and_owner_loss(self):
        result=subprocess.run(['dbus-run-session','--',sys.executable,str(Path(__file__).with_name('fixtures')/'session_lock_live.py')],
                              env=os.environ, capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('desktop lock transports passed',result.stdout)
