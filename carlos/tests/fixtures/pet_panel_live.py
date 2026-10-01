"""Opt-in native pet IPC policy check in an isolated D-Bus session, offscreen."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time

from ev.events import PhaxEventBus
from ev.ipc.server import IpcServer


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pet-binary',type=Path,required=True)
    args=parser.parse_args()
    binary=args.pet_binary.resolve(strict=True)
    if not os.access(binary,os.X_OK):raise ValueError('Pet binary is not executable')
    with tempfile.TemporaryDirectory(prefix='carlos-pet-panel-') as temp:
        root=Path(temp)
        (root/'ev').mkdir(mode=0o700)
        bus=PhaxEventBus()
        state={'privacy_mode':False,'session_locked':False,'state':'DORMANT'}
        async def handler(request):
            assert request['type']=='panel.summary'
            return dict(state)
        server=IpcServer(root/'ev/ev.sock',bus,handler,logging.getLogger('pet-panel-fixture'))
        await server.start()
        env=dict(os.environ,XDG_RUNTIME_DIR=str(root),XDG_CONFIG_HOME=str(root/'config'),QT_QPA_PLATFORM='offscreen')
        log=(root/'pet.log').open('wb')
        pet=await asyncio.create_subprocess_exec(str(binary),env=env,stdout=log,stderr=log)
        checks=[]
        async def shown():
            proc=await asyncio.create_subprocess_exec('gdbus','call','--session','--dest','org.phax.CarlosPet',
                '--object-path','/Pet','--method','org.freedesktop.DBus.Properties.Get','org.phax.CarlosPet','shown',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,err=await proc.communicate()
            if proc.returncode:return None
            assert out.strip() in (b'(<true>,)',b'(<false>,)'),out
            return out.strip()==b'(<true>,)'
        async def wait(expected,timeout=3):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if pet.returncode is not None:raise RuntimeError('Native pet exited')
                if await shown() is expected:return
                await asyncio.sleep(.02)
            raise RuntimeError('Native pet did not reach expected visibility policy')
        try:
            await wait(True);checks.append('fresh unlocked session')
            for locked, expected in ((True,False),(None,False),(False,True)):
                state['session_locked']=locked
                bus.publish('presence.session_changed','fixture',{})
                await wait(expected)
            checks.append('lock, unavailable owner and fresh unlock')
            state['privacy_mode']=True;bus.publish('carlos.privacy_transition','fixture',{})
            await wait(False)
            state['privacy_mode']=False;bus.publish('carlos.privacy_changed','fixture',{})
            await wait(True);checks.append('privacy hide and restore')
            await server.stop();await wait(False);checks.append('core disconnect hides immediately')
            await server.start()
            state['session_locked']=True
            await asyncio.sleep(15.1)
            assert await shown() is False,'Reconnect reused old unlocked state'
            state['session_locked']=False;bus.publish('presence.session_changed','fixture',{})
            await wait(True);checks.append('reconnect requires fresh unlock')
            print(json.dumps({'scope':'Actual native Qt/IPC policy, isolated D-Bus and offscreen; no physical lock or compositor acceptance',
                              'checks':checks,'host_settings_changed':False},indent=2))
        finally:
            await server.stop()
            if pet.returncode is None:pet.terminate()
            await asyncio.wait_for(pet.wait(),3)
            log.close()


asyncio.run(main())
