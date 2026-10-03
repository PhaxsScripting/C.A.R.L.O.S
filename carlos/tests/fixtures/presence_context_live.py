import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))


async def check(root):
    from ev.paths import Paths
    from ev.service import CarlosCore

    core = CarlosCore(paths=Paths(*(root / name for name in ('config', 'data', 'state', 'cache', 'run'))))
    subscriber, queue = core.bus.subscribe()
    persistence = asyncio.create_task(core._persist_events(queue))
    writer = None
    sequence = 0
    report = {'scope': 'Actual private Core event persistence and IPC; synthetic lock and hand observations',
              'physical_presence_verified': False, 'host_session_locked': False,
              'camera_started': False, 'desktop_actions': 0}
    try:
        await core.ipc.start()
        reader, writer = await asyncio.open_unix_connection(str(core.paths.socket))
        await reader.readline()

        async def snapshot():
            nonlocal sequence
            await asyncio.wait_for(queue.join(), 2)
            sequence += 1
            request_id = f'presence-{sequence}'
            writer.write((json.dumps({'id': request_id, 'type': 'snapshot', 'payload': {}}) + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(2):
                while True:
                    line = await reader.readline()
                    assert line, 'Private Core closed the connection'
                    response = json.loads(line)
                    if response.get('id') == request_id:
                        assert response['type'] == 'response'
                        return response['payload']['presence']

        core.presence.observe_lock(False)
        core.bus.publish('command.received', 'presence-fixture')
        assert (await snapshot())['presence'] == 'ENGAGED'
        core.presence.observe_lock(True)
        for kind in ('wake.detected', 'command.received', 'voice.transcription_complete',
                     'voice.listening_started', 'voice.barge_in', 'carlos.privacy_changed'):
            core.bus.publish(kind, 'presence-fixture')
            state = await snapshot()
            assert state['session'] == 'LOCKED' and state['presence'] == 'LIKELY_ABSENT'
            assert state['attention'] == 'DORMANT' and core.presence.last_addressed == 0
        core.presence.observe_lock(False)
        assert (await snapshot())['presence'] == 'UNKNOWN'
        core.presence.state['idle_seconds'] = 10
        sample = {'state': 'READY', 'tracking': {'hand_visible': True, 'confidence': .95, 'age_ms': 30}}
        core.presence.observe_hand(sample)
        state = await snapshot()
        assert state['presence'] == 'AT_DESK' and state['person_identity'] == 'UNVERIFIED'
        core.bus.publish('carlos.privacy_changed', 'presence-fixture')
        state = await snapshot()
        assert state['presence'] == 'UNKNOWN' and not state['camera_used']
        sample['tracking']['confidence'] = float('inf')
        core.presence.observe_hand(sample)
        assert (await snapshot())['presence'] == 'UNKNOWN'
        core.bus.publish('command.received', 'presence-fixture')
        assert (await snapshot())['presence'] == 'ENGAGED'
        assert core.task_journal.recent() == []
        report.update(locked_activity_refused=True, unlock_does_not_reuse_old_address=True,
                      malformed_hand_reading_unknown=True, identity_remains_unverified=True,
                      privacy_cleared_camera_evidence=True, next_explicit_interaction_engaged=True)
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        persistence.cancel()
        await asyncio.gather(persistence, return_exceptions=True)
        core.bus.unsubscribe(subscriber)
        await core.ipc.stop()
        await core.voice.close()
        if hasattr(core.brain.provider, 'close'):
            await core.brain.provider.close()
        core.daily.close()
        core.memory.close()
        core.task_journal.close()
        report['owned_core_cleanup_completed'] = True
    return report


def run():
    with tempfile.TemporaryDirectory(prefix='carlos-presence-test-') as directory:
        report = asyncio.run(check(Path(directory)))
    report['temporary_runtime_removed'] = True
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    run()
