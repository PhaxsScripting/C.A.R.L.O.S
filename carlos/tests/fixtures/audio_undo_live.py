import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))


def run():
    with tempfile.TemporaryDirectory(prefix='carlos-audio-test-') as directory:
        root = Path(directory)
        runtime = root / 'run'
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'),
                   PIPEWIRE_CONFIG_DIR=str(root), PIPEWIRE_CONFIG_PREFIX='',
                   PIPEWIRE_RUNTIME_DIR=str(runtime), PIPEWIRE_REMOTE='pipewire-0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'no-session-bus'),
                   PULSE_SERVER='unix:' + str(runtime / 'pulse/native'))
        server = root / 'pipewire.conf'
        shutil.copyfile('/usr/share/pipewire/client.conf', root / 'client.conf')
        server.write_text('''context.properties = { core.daemon = true core.name = pipewire-0 }
context.spa-libs = { support.* = support/libspa-support audio.convert.* = audioconvert/libspa-audioconvert }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-spa-node-factory }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-access }
]
context.objects = [ { factory = metadata args = { metadata.name = default } } ]
''')
        pulse = root / 'pulse.conf'
        pulse.write_text('''context.spa-libs = { support.* = support/libspa-support audio.convert.* = audioconvert/libspa-audioconvert }
context.modules = [
 { name = libpipewire-module-protocol-native }
 { name = libpipewire-module-client-node }
 { name = libpipewire-module-adapter }
 { name = libpipewire-module-metadata }
 { name = libpipewire-module-protocol-pulse args = { server.address = [ "unix:native" ] } }
]
''')

        def pactl(*args):
            result = subprocess.run(['pactl', *args], env=env, text=True,
                                    capture_output=True, timeout=2)
            if result.returncode:
                raise RuntimeError(result.stderr)
            return result.stdout.strip()

        children = []
        logs = root / 'server.log'
        with logs.open('w') as log:
            try:
                children.append(subprocess.Popen(['pipewire', '-c', server.name], env=env,
                                                 stdout=log, stderr=log))
                deadline = time.monotonic() + 4
                while not (runtime / 'pipewire-0').exists():
                    if time.monotonic() > deadline or children[0].poll() is not None:
                        raise RuntimeError('Private PipeWire failed: ' + logs.read_text())
                    time.sleep(.02)
                children.append(subprocess.Popen(['pipewire-pulse', '-c', pulse.name], env=env,
                                                 stdout=log, stderr=log))
                while not (runtime / 'pulse/native').exists():
                    if time.monotonic() > deadline or children[1].poll() is not None:
                        raise RuntimeError('Private Pulse server failed: ' + logs.read_text())
                    time.sleep(.02)
                # This environment belongs to the fixture process only.
                os.environ.update(env)
                from ev.events import PhaxEventBus
                from ev.tools import ToolContext, builtin
                from ev.tools.audio_undo import snapshot, undo_last

                pactl('load-module', 'module-null-sink', 'sink_name=carlos_undo_fixture',
                      'channels=2', 'channel_map=front-left,front-right')
                pactl('set-default-sink', 'carlos_undo_fixture')
                configured = subprocess.run(['pw-metadata', '-n', 'default', '0',
                                             'default.audio.sink', '{"name":"carlos_undo_fixture"}',
                                             'Spa:String:JSON'], env=env, capture_output=True,
                                            text=True, timeout=2)
                assert configured.returncode == 0, configured.stderr
                deadline = time.monotonic() + 2
                while pactl('get-default-sink') != 'carlos_undo_fixture':
                    if time.monotonic() > deadline:
                        raise RuntimeError('Private default output did not settle: ' + pactl('info'))
                    time.sleep(.02)
                pactl('set-sink-volume', 'carlos_undo_fixture', '35%', '65%')
                context = ToolContext({}, PhaxEventBus(), logging.getLogger('native-audio-undo'))
                initial = snapshot('carlos_undo_fixture')
                assert initial is not None, pactl('-f', 'json', 'list', 'sinks')
                assert builtin.set_volume({'percent': 25}, context)['undo_available']
                pactl('set-sink-mute', 'carlos_undo_fixture', '1')
                assert undo_last({}, context)['verified']
                restored = snapshot('carlos_undo_fixture')
                assert restored['volume'] == initial['volume'] and restored['mute'] is True
                assert builtin.set_mute({'muted': False}, context)['undo_available']
                assert undo_last({}, context)['verified']
                assert snapshot('carlos_undo_fixture')['mute'] is True
                assert builtin.adjust_volume({'delta': 10}, context)['undo_available']
                pactl('set-sink-volume', 'carlos_undo_fixture', '17%', '19%')
                overridden = snapshot('carlos_undo_fixture')
                assert undo_last({}, context)['verified'] is False
                assert snapshot('carlos_undo_fixture') == overridden
                assert pactl('get-default-sink') == 'carlos_undo_fixture'
                print(json.dumps({'result': 'isolated audio undo passed',
                                  'restored_channels': restored['volume'],
                                  'manual_channels_kept': overridden['volume'],
                                  'host_playback_touched': False}))
            finally:
                for child in reversed(children):
                    if child.poll() is None:
                        child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=3)


if __name__ == '__main__':
    run()
