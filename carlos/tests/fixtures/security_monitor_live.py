import asyncio
import json
import tempfile
import time
from pathlib import Path

from ev.paths import Paths
from ev.security_sources import fingerprint, observation
from ev.service import CarlosCore


async def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        core = CarlosCore(paths=Paths(*(root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
        entered, release = asyncio.Event(), asyncio.Event()
        rows = {name: {} for name in ('listeners', 'tailscale', 'failed_logins', 'services', 'firewall', 'smart', 'updates')}
        rows['smart']['disk'] = 'PASSED'
        startup = root / 'fixture-startup.sh'
        startup.write_text('FIXTURE_CANARY first version')

        class Sources:
            async def read(self, name):
                entered.set()
                await release.wait()
                return observation('fixture', dict(rows[name]), count=len(rows[name]))

            def startup(self):
                return observation('fixture', {fingerprint(str(startup)): fingerprint(startup.read_text())}, count=1)

        sources = Sources()
        for name in rows:
            setattr(sources, name, lambda name=name: sources.read(name))
        monitor = core.security_monitor
        monitor.sources = sources
        subscriber, queue = core.bus.subscribe()
        persistence = asyncio.create_task(core._persist_events(queue))
        poll = asyncio.create_task(monitor.poll())
        writer = None
        try:
            await core.ipc.start()
            await asyncio.wait_for(entered.wait(), 2)
            reader, writer = await asyncio.open_unix_connection(str(core.paths.socket))
            await reader.readline()

            async def request(kind, payload, identity):
                writer.write((json.dumps({'type': kind, 'id': identity, 'payload': payload}) + '\n').encode())
                await writer.drain()
                while True:
                    line = await asyncio.wait_for(reader.readline(), 2)
                    assert line, 'IPC closed'
                    reply = json.loads(line)
                    if reply.get('id') == identity:
                        assert reply.get('type') != 'error', reply
                        return reply['payload']

            start = time.perf_counter()
            health = await request('health', {}, 'blocked-health')
            elapsed = (time.perf_counter() - start) * 1000
            assert health['ok'] and not release.is_set()
            core.bus.publish('system.telemetry', 'fixture', {'fixture_counter': 18})
            await asyncio.wait_for(queue.join(), 2)
            assert core.latest_telemetry == {'fixture_counter': 18}
            release.set()
            await poll
            rows['listeners']['new-listener'] = 'NETWORK_BOUND'
            rows['tailscale']['new-device'] = 'VISIBLE'
            rows['failed_logins']['new-attempt'] = 'FAILED'
            rows['services']['smartd'] = 'CRASHED'
            rows['smart']['disk'] = 'FAILED'
            startup.write_text('FIXTURE_CANARY updated version')
            monitor._heavy_due['smart'] = 0
            await monitor.poll()
            await asyncio.wait_for(queue.join(), 2)
            events = [event for event in core.bus.history(100) if event['type'] in {'security.alert', 'security.observed'}]
            assert len(events) == 6, events
            assert sum(event['priority'] == 'HIGH' for event in events) == 2
            assert all(event['priority'] != 'EMERGENCY' for event in events)
            notice_metrics = core._notification_queue.metrics()
            assert notice_metrics['queued'] == 6, notice_metrics
            result = await request('tool.call', {'name': 'security.monitor', 'arguments': {}}, 'monitor-read')
            assert result['status'] == 'completed' and not result['execution']['changed_state'], result
            assert 'FIXTURE_CANARY' not in json.dumps(result)
            receipt_count = len(core.task_journal.recent())
            print(json.dumps({'scope': 'Temporary actual core IPC with simulated blocked probes and fixture startup files',
                              'health_response_ms': round(elapsed, 3), 'telemetry_progressed': True,
                              'change_events': len(events), 'high_events': 2, 'emergency_events': 0,
                              'security_notices_queued': notice_metrics['queued'],
                              'monitor_tool_changed_state': False, 'action_tasks_created': receipt_count,
                              'raw_fixture_content_excluded': True}))
        finally:
            release.set()
            poll.cancel()
            persistence.cancel()
            await asyncio.gather(poll, persistence, return_exceptions=True)
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            core.bus.unsubscribe(subscriber)
            await core.ipc.stop()
            core.daily.close()
            core.task_journal.close()
            core.memory.close()


asyncio.run(main())
