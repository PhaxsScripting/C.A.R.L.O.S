"""Native GI worker; screen grants and encoders stay out of the Core process."""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

PORTAL = 'org.freedesktop.portal.Desktop'
ROOT = '/org/freedesktop/portal/desktop'
SCREENCAST = 'org.freedesktop.portal.ScreenCast'
MAXIMUM_BYTES = 64 * 1024 * 1024


def native_stack():
    registry = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ev/recording'
    registry.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(registry, 0o700)
    os.environ['GST_REGISTRY_1_0'] = str(registry / 'gstreamer-registry.bin')
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gio, GLib, Gst
    Gst.init(None)
    plugins = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'ev/deps/gstreamer-1.0'
    if plugins.is_dir():
        Gst.Registry.get().scan_path(str(plugins))
    return Gio, GLib, Gst


def codec(Gst):
    for name in ('x264enc', 'vp8enc'):
        if Gst.ElementFactory.find(name):
            return name
    return ''


def probe():
    try:
        Gio, GLib, Gst = native_stack()
        required = ('pipewiresrc', 'videoconvert', 'videoscale', 'videorate', 'matroskamux', 'filesink')
        missing = [name for name in required if not Gst.ElementFactory.find(name)]
        if not shutil.which('ffprobe'):
            missing.append('ffprobe')
        encoder = codec(Gst)
        if not encoder:
            missing.append('x264enc or vp8enc')
        source = Gst.ElementFactory.make('pipewiresrc')
        if source is not None and source.find_property('keepalive-time') is None:
            missing.append('PipeWire source with keepalive support')
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = connection.call_sync(PORTAL, ROOT, 'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', (SCREENCAST,)), GLib.VariantType.new('(a{sv})'),
            Gio.DBusCallFlags.NONE, 2000, None)
        properties = reply.unpack()[0]
        sources = int(properties.get('AvailableSourceTypes', 0)) & 3
        return {'available': not missing and bool(sources), 'missing': missing, 'encoder': encoder,
                'portal_version': int(properties.get('version', 0)), 'source_types': sources,
                'reason': 'Dependencies and portal advertised; recording still needs a native grant'
                          if not missing and sources else 'Missing recording dependencies or portal sources'}
    except Exception as error:
        return {'available': False, 'missing': [], 'reason': str(error)[:400]}


class ScreenCastSession:
    def __init__(self, Gio, GLib):
        self.Gio, self.GLib = Gio, GLib
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = ''
        self.closed = False
        self._closed_subscription = 0
        self.deadline = time.monotonic() + 45

    def request(self, method, signature, values, options):
        Gio, GLib = self.Gio, self.GLib
        token = 'carlos_' + uuid.uuid4().hex
        sender = self.connection.get_unique_name()[1:].replace('.', '_')
        path = ROOT + '/request/' + sender + '/' + token
        loop = GLib.MainLoop()
        result = []

        def response(connection, sender, object_path, interface, member, parameters, user_data):
            result.append(parameters.unpack())
            loop.quit()

        subscription = self.connection.signal_subscribe(PORTAL, 'org.freedesktop.portal.Request',
            'Response', path, None, Gio.DBusSignalFlags.NONE, response, None)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.connection.signal_unsubscribe(subscription)
            raise RuntimeError('Screen selection timed out')
        timer = GLib.timeout_add(max(1, int(remaining * 1000)), lambda: (loop.quit(), False)[1])
        try:
            options = {**options, 'handle_token': GLib.Variant('s', token)}
            reply = self.connection.call_sync(PORTAL, ROOT, SCREENCAST, method,
                GLib.Variant(signature, (*values, options)), GLib.VariantType.new('(o)'),
                Gio.DBusCallFlags.NONE, min(5000, int(remaining * 1000)), None)
            if reply.unpack()[0] != path:
                raise RuntimeError('Portal returned an unexpected request handle')
            if not result:
                loop.run()
            if not result:
                raise RuntimeError('Screen selection timed out')
            code, data = result[0]
            if code != 0:
                raise RuntimeError('Screen recording permission was cancelled or denied')
            return data
        finally:
            source = GLib.MainContext.default().find_source_by_id(timer)
            if source is not None:
                source.destroy()
            self.connection.signal_unsubscribe(subscription)
            if not result:
                try:
                    self.connection.call_sync(PORTAL, path, 'org.freedesktop.portal.Request', 'Close',
                        None, None, Gio.DBusCallFlags.NONE, 1000, None)
                except Exception:
                    pass

    def open(self):
        Gio, GLib = self.Gio, self.GLib
        data = self.request('CreateSession', '(a{sv})', (),
            {'session_handle_token': GLib.Variant('s', 'carlos_' + uuid.uuid4().hex)})
        self.session = data['session_handle']
        if not isinstance(self.session, str) or not self.session.startswith(ROOT + '/session/'):
            raise RuntimeError('Portal returned an invalid session')

        def closed(*args):
            self.closed = True

        self._closed_subscription = self.connection.signal_subscribe(PORTAL,
            'org.freedesktop.portal.Session', 'Closed', self.session, None,
            Gio.DBusSignalFlags.NONE, closed, None)
        properties = self.connection.call_sync(PORTAL, ROOT, 'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', (SCREENCAST,)), GLib.VariantType.new('(a{sv})'),
            Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        sources = int(properties.get('AvailableSourceTypes', 0)) & 3
        if not sources:
            raise RuntimeError('Portal has no screen or window source')
        options = {'types': GLib.Variant('u', sources), 'multiple': GLib.Variant('b', False)}
        if int(properties.get('version', 0)) >= 2 and int(properties.get('AvailableCursorModes', 0)) & 2:
            options['cursor_mode'] = GLib.Variant('u', 2)
        self.request('SelectSources', '(oa{sv})', (self.session,), options)
        streams = self.request('Start', '(osa{sv})', (self.session, ''), {}).get('streams', [])
        if len(streams) != 1 or self.closed:
            raise RuntimeError('Portal did not grant exactly one live recording source')
        node, metadata = streams[0]
        serial = metadata.get('pipewire-serial')
        if serial is not None:
            if not isinstance(serial, int) or isinstance(serial, bool) or serial <= 0:
                raise RuntimeError('Portal returned an invalid stream serial')
            target = ('target-object', str(serial))
        else:
            if not isinstance(node, int) or isinstance(node, bool) or node <= 0:
                raise RuntimeError('Portal returned an invalid stream node')
            target = ('path', str(node))
        reply, fds = self.connection.call_with_unix_fd_list_sync(PORTAL, ROOT, SCREENCAST,
            'OpenPipeWireRemote', GLib.Variant('(oa{sv})', (self.session, {})),
            GLib.VariantType.new('(h)'), Gio.DBusCallFlags.NONE, 5000, None, None)
        return fds.get(reply.unpack()[0]), target

    def close(self):
        if self.session:
            try:
                self.connection.call_sync(PORTAL, self.session, 'org.freedesktop.portal.Session',
                    'Close', None, None, self.Gio.DBusCallFlags.NONE, 1000, None)
            except Exception:
                pass
            self.session = ''
        if self._closed_subscription:
            self.connection.signal_unsubscribe(self._closed_subscription)
            self._closed_subscription = 0


def record_pipeline(source, output, seconds, encoder, Gst, GLib, revoked=lambda: False):
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError('Recording duration must be 1 to 120 seconds')
    if encoder not in ('x264enc', 'vp8enc'):
        raise ValueError('Unsupported recording encoder')
    encode = ('x264enc threads=2 bitrate=2500 speed-preset=veryfast tune=zerolatency'
              if encoder == 'x264enc' else 'vp8enc threads=2 target-bitrate=2500000 cpu-used=5 deadline=1')
    pipeline = Gst.parse_launch('queue name=frames max-size-buffers=4 max-size-bytes=0 max-size-time=0 ! '
        'videoconvert ! videoscale add-borders=true ! videorate drop-only=true ! '
        'video/x-raw,width=1280,height=720,framerate=15/1,pixel-aspect-ratio=1/1 ! ' + encode +
        ' name=encoder ! matroskamux ! filesink name=output')
    with output.open('xb'):
        pass
    os.chmod(output, 0o600)
    pipeline.get_by_name('output').set_property('location', str(output))
    first = pipeline.get_by_name('frames')
    pipeline.add(source)
    if not source.link(first):
        raise RuntimeError('Could not link the granted screen source')
    loop = GLib.MainLoop()
    state = {'frames': 0, 'first_frame': None, 'eos_sent': None, 'error': '', 'finished': False}
    started = time.monotonic()

    def frame(pad, info):
        state['frames'] += 1
        if state['first_frame'] is None:
            state['first_frame'] = time.monotonic()
        return Gst.PadProbeReturn.OK

    pipeline.get_by_name('encoder').get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, frame)

    def message(bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            state['error'] = str(error)[:400]
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            state['finished'] = state['eos_sent'] is not None
            loop.quit()

    def poll():
        now = time.monotonic()
        try:
            if revoked():
                state['error'] = 'Native screen grant was revoked'
            elif output.stat().st_size > MAXIMUM_BYTES:
                state['error'] = 'Recording exceeded its 64 MiB limit'
            else:
                space = os.statvfs(output.parent)
                if space.f_bavail * space.f_frsize < 16 * 1024 * 1024:
                    state['error'] = 'Not enough free storage to continue recording'
        except Exception:
            state['error'] = 'Recording source or storage became unavailable'
        if state['first_frame'] is None and now - started > 10:
            state['error'] = 'Granted screen produced no frames within ten seconds'
        elif state['eos_sent'] is not None and now - state['eos_sent'] > 3:
            state['error'] = 'Encoder did not finish its recording'
        if state['error']:
            loop.quit()
            return False
        if state['first_frame'] is not None and now - state['first_frame'] >= seconds and state['eos_sent'] is None:
            state['eos_sent'] = now
            if not pipeline.send_event(Gst.Event.new_eos()):
                state['error'] = 'Encoder rejected the finish request'
                loop.quit()
                return False
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    handler = bus.connect('message', message)
    timer = GLib.timeout_add(100, poll)
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Recording pipeline did not start')
        loop.run()
        if state['error'] or not state['finished'] or not state['frames']:
            raise RuntimeError(state['error'] or 'Recording ended before its requested duration')
    finally:
        pipeline.set_state(Gst.State.NULL)
        source_timer = GLib.MainContext.default().find_source_by_id(timer)
        if source_timer is not None:
            source_timer.destroy()
        bus.disconnect(handler)
        bus.remove_signal_watch()
    result = subprocess.run([shutil.which('ffprobe') or 'ffprobe', '-v', 'error', '-select_streams', 'v',
        '-show_entries', 'stream=codec_name,width,height:format=duration,format_name',
        '-of', 'json', str(output)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=5, check=False)
    if result.returncode != 0 or len(result.stdout) > 16384:
        raise RuntimeError('The recording container could not be inspected')
    report = json.loads(result.stdout)
    streams = report.get('streams', [])
    duration = float(report.get('format', {}).get('duration', 0))
    if ('matroska' not in report.get('format', {}).get('format_name', '').split(',')
        or len(streams) != 1 or not max(.01, seconds - .2) <= duration <= 125
        or streams[0].get('codec_name') not in {'h264', 'vp8'}):
        raise RuntimeError('The recording could not be verified as a complete video')
    return {'frames': state['frames'], 'duration_seconds': duration,
            'width': streams[0]['width'], 'height': streams[0]['height'], 'encoder': encoder}


def capture(output, seconds):
    Gio, GLib, Gst = native_stack()
    portal = ScreenCastSession(Gio, GLib)
    fd = None
    try:
        fd, target = portal.open()
        source = Gst.ElementFactory.make('pipewiresrc', 'screen')
        if source is None or source.find_property('keepalive-time') is None:
            raise RuntimeError('PipeWire source cannot maintain a static recording timeline')
        source.set_property('fd', fd)
        source.set_property(*target)
        source.set_property('keepalive-time', 67)
        return record_pipeline(source, output, seconds, codec(Gst), Gst, GLib,
                               revoked=lambda: portal.closed)
    finally:
        portal.close()
        if fd is not None:
            os.close(fd)


def main():
    os.umask(0o077)
    if sys.platform.startswith('linux'):
        import ctypes
        import signal
        parent = os.getppid()
        if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            print(json.dumps({'error': 'Could not bind the recording worker to its parent lifetime'}))
            return 2
    if sys.argv[1:] == ['--probe']:
        print(json.dumps(probe()))
        return 0
    output = None
    try:
        if len(sys.argv) != 3:
            raise ValueError('Expected recording path and duration')
        candidate = Path(sys.argv[1]).absolute()
        if candidate.suffix != '.mkv' or candidate.exists() or candidate.is_symlink():
            raise ValueError('Expected a new Matroska recording path')
        output = candidate
        result = capture(output, int(sys.argv[2]))
        print(json.dumps(result, allow_nan=False))
        return 0
    except Exception as error:
        if output is not None:
            output.unlink(missing_ok=True)
        print(json.dumps({'error': str(error)[:400]}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
