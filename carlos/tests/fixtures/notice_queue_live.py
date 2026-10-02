import asyncio
import json
import tempfile
import time
from pathlib import Path

from ev.paths import Paths
from ev.service import CarlosCore


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        core = CarlosCore(paths=Paths(*(root / n for n in ('config','data','state','cache','run'))))
        blocked, release = asyncio.Event(), asyncio.Event()
        delivered = []

        async def notify(event):
            delivered.append(event)
            if len(delivered) == 1:
                blocked.set()
                await release.wait()

        core._notify_event = notify
        subscriber, queue = core.bus.subscribe()
        persistence = asyncio.create_task(core._persist_events(queue))
        delivery = asyncio.create_task(core._deliver_notifications())
        writer = None
        try:
            await core.ipc.start()
            core.bus.publish('system.error', 'fixture', {'message':'Held fixture notification'})
            await asyncio.wait_for(blocked.wait(), 2)
            warning = core.bus.publish('system.warning', 'telemetry', {'kind':'thermal','celsius':99,
                                                                      'message':'Synthetic fixture warning'})
            for i in range(100):
                core.bus.publish('tool.completed', 'tools', {'tool':'development.fixture','index':i})
            core.bus.publish('system.telemetry', 'fixture', {'fixture_counter':17})
            await asyncio.wait_for(queue.join(), 2)
            assert core.latest_telemetry == {'fixture_counter':17}
            metrics = core._notification_queue.metrics()
            assert metrics['queued'] == 32 and metrics['dropped'] == 69,metrics
            assert metrics['dropped_by_priority']['EMERGENCY'] == 0,metrics
            reader, writer = await asyncio.open_unix_connection(str(core.paths.socket))
            await reader.readline()
            started = time.monotonic()
            writer.write(b'{"type":"health","id":"notice-health","payload":{}}\n')
            await writer.drain()
            while True:
                line = await asyncio.wait_for(reader.readline(), 2)
                assert line
                reply = json.loads(line)
                if reply.get('id') == 'notice-health':
                    assert reply.get('type') != 'error' and reply['payload']['ok'],reply
                    break
            health_ms = (time.monotonic()-started)*1000
            assert not release.is_set()
            release.set()
            await asyncio.wait_for(core._notification_queue.join(), 2)
            assert delivered[1] is warning
            assert core.task_journal.recent() == []
            print(json.dumps({'scope':'Temporary real core IPC/persistence with a simulated blocked notifier',
                              'health_response_ms':round(health_ms,3), 'telemetry_progressed':True,
                              'emergency_delivered_first_after_release':True,
                              'queue_metrics_before_release':metrics,'actions_executed':0}))
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            persistence.cancel(); delivery.cancel()
            await asyncio.gather(persistence, delivery, return_exceptions=True)
            core.bus.unsubscribe(subscriber)
            await core.ipc.stop()
            core.memory.close()
            core.task_journal.close()


asyncio.run(main())
