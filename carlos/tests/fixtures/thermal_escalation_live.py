import asyncio
import json
import tempfile
import time
from pathlib import Path

from ev.paths import Paths
from ev.service import CarlosCore
from ev.telemetry import TelemetrySampler


async def main():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        core=CarlosCore(paths=Paths(*(root/name for name in ('config','data','state','cache','run'))))
        blocked,release,stop=asyncio.Event(),asyncio.Event(),asyncio.Event()
        delivered=[]
        async def notify(event):
            delivered.append(event)
            if len(delivered)==1:
                blocked.set()
                await release.wait()
        core._notify_event=notify
        subscriber,queue=core.bus.subscribe()
        persistence=asyncio.create_task(core._persist_events(queue))
        delivery=asyncio.create_task(core._deliver_notifications())
        sampler=TelemetrySampler(core.bus,config={'warning_repeat_seconds':120})
        sampler.interval=.01
        samples=iter([91,99,99])
        sampler.sample=lambda:{'cpu_temperature':{'celsius':next(samples,99),'sensor':'synthetic-fixture'},
                               'resource_mode':'NORMAL','memory':{'available_bytes':1000000},
                               'network':{'link_state':'UP'},'battery':None}
        telemetry=asyncio.create_task(sampler.run(stop))
        writer=None
        try:
            await core.ipc.start()
            await asyncio.wait_for(blocked.wait(),2)
            async with asyncio.timeout(2):
                while len([e for e in core.bus.history() if e['type']=='system.warning'])<2:
                    await asyncio.sleep(.005)
            stop.set()
            await telemetry
            for i in range(10):
                core.bus.publish('tool.completed','tools',{'tool':'development.fixture','index':i})
            await asyncio.wait_for(queue.join(),2)
            warnings=[e for e in core.bus.history() if e['type']=='system.warning']
            assert [e['priority'] for e in warnings]==['HIGH','EMERGENCY'],warnings
            reader,writer=await asyncio.open_unix_connection(str(core.paths.socket))
            await reader.readline()
            before=time.monotonic()
            writer.write(b'{"id":"thermal-health","type":"health","payload":{}}\n')
            await writer.drain()
            while True:
                line=await asyncio.wait_for(reader.readline(),2)
                assert line
                response=json.loads(line)
                if response.get('id')=='thermal-health':
                    assert response['type']=='response' and response['payload']['ok'],response
                    break
            elapsed=(time.monotonic()-before)*1000
            release.set()
            await asyncio.wait_for(core._notification_queue.join(),2)
            assert delivered[1].priority=='EMERGENCY'
            assert core.task_journal.recent()==[]
            print(json.dumps({'scope':'Actual temporary Core IPC/persistence and producer; synthetic temperatures, held notifier',
                              'warning_priorities':[e['priority'] for e in warnings],
                              'emergency_delivered_first_after_release':True,'health_response_ms':round(elapsed,3),
                              'actions_executed':0,'real_thermal_stress':False,'acoustic_preemption_verified':False}))
        finally:
            stop.set()
            if writer:
                writer.close()
                await writer.wait_closed()
            persistence.cancel()
            delivery.cancel()
            telemetry.cancel()
            await asyncio.gather(persistence,delivery,telemetry,return_exceptions=True)
            core.bus.unsubscribe(subscriber)
            await core.ipc.stop()
            await core.voice.close()
            if hasattr(core.brain.provider,'close'):
                await core.brain.provider.close()
            core.daily.close()
            core.memory.close()
            core.task_journal.close()


asyncio.run(main())
