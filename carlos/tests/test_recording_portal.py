import os
import unittest
from unittest.mock import Mock, patch

from ev import recording_worker as worker


class Variant:
    def __init__(self, signature, value):
        self.value = value

    def unpack(self):
        return self.value


class Loop:
    def __init__(self, glib):
        self.glib = glib

    def run(self):
        for source in tuple(self.glib.sources.values()):
            if not source.destroyed:
                source.callback()

    def quit(self):
        pass


class GLib:
    Variant = Variant
    VariantType = Mock(new=lambda value: value)

    def __init__(self):
        self.sources = {}
        self.MainContext = Mock(default=lambda: self)

    def MainLoop(self):
        return Loop(self)

    def timeout_add(self, delay, callback):
        ident = len(self.sources) + 1
        source = Mock(callback=callback, destroyed=False)
        source.destroy.side_effect = lambda: setattr(source, 'destroyed', True)
        self.sources[ident] = source
        return ident

    def find_source_by_id(self, ident):
        return self.sources.get(ident)


class Connection:
    def __init__(self, fd):
        self.fd = fd
        self.subscriptions = {}
        self.calls = []
        self.node = 42
        self.serial = None
        self.response_code = 0
        self.ignore_response = False
        self.revoke_on_start = False
        self.returned_fd = None

    def get_unique_name(self):
        return ':1.99'

    def signal_subscribe(self, sender, interface, member, path, arg0, flags, callback, user_data):
        ident = max(self.subscriptions, default=0) + 1
        self.subscriptions[ident] = (member, path, callback)
        return ident

    def signal_unsubscribe(self, ident):
        self.subscriptions.pop(ident)

    def call_sync(self, service, path, interface, method, parameters, reply_type, flags, timeout, cancellable):
        self.calls.append(method)
        if method == 'GetAll':
            return Variant('', ({'version': 6, 'AvailableSourceTypes': 3, 'AvailableCursorModes': 2},))
        if method == 'Close':
            return Variant('', ())
        options = parameters.unpack()[-1]
        request_path = worker.ROOT + '/request/1_99/' + options['handle_token'].unpack()
        if method == 'CreateSession':
            data = {'session_handle': worker.ROOT + '/session/1_99/fixture'}
        elif method == 'Start':
            data = {'streams': [(self.node, {'pipewire-serial': self.serial} if self.serial is not None else {})]}
            if self.revoke_on_start:
                for member, path, callback in tuple(self.subscriptions.values()):
                    if member == 'Closed':
                        callback()
        else:
            data = {}
        if not self.ignore_response:
            for member, path, callback in tuple(self.subscriptions.values()):
                if member == 'Response' and path == request_path:
                    callback(None, None, path, None, member, Variant('', (self.response_code, data)), None)
        return Variant('', (request_path,))

    def call_with_unix_fd_list_sync(self, *args):
        self.calls.append('OpenPipeWireRemote')
        self.returned_fd = os.dup(self.fd)
        return Variant('', (0,)), Mock(get=lambda index: self.returned_fd)


class RecordingPortalTests(unittest.TestCase):
    def setUp(self):
        self.read, self.write = os.pipe()
        self.connection = Connection(self.read)
        self.glib = GLib()
        self.gio = Mock()
        self.gio.bus_get_sync.return_value = self.connection
        self.session = worker.ScreenCastSession(self.gio, self.glib)

    def tearDown(self):
        self.session.close()
        os.close(self.read)
        os.close(self.write)
        fd = self.connection.returned_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def test_requests_wait_for_grant_before_opening_remote(self):
        fd, target = self.session.open()
        self.assertEqual(target, ('path', '42'))
        self.assertEqual(self.connection.calls, ['CreateSession', 'GetAll', 'SelectSources', 'Start', 'OpenPipeWireRemote'])
        self.assertGreaterEqual(fd, 0)
        self.assertTrue(all(source.destroyed for source in self.glib.sources.values()))
        self.session.close()
        self.assertEqual(self.connection.subscriptions, {})

    def test_serial_is_preferred_to_reusable_node_identifier(self):
        self.connection.serial = 900001
        _, target = self.session.open()
        self.assertEqual(target, ('target-object', '900001'))

    def test_denial_timeout_and_revoked_session_never_open_remote(self):
        for kind in ('denied', 'timeout', 'revoked'):
            with self.subTest(kind=kind):
                self.connection.response_code = 1 if kind == 'denied' else 0
                self.connection.ignore_response = kind == 'timeout'
                self.connection.revoke_on_start = kind == 'revoked'
                self.connection.calls.clear()
                with self.assertRaises(RuntimeError):
                    self.session.open()
                self.assertNotIn('OpenPipeWireRemote', self.connection.calls)
                self.session.close()
                self.assertEqual(self.connection.subscriptions, {})
                self.session = worker.ScreenCastSession(self.gio, self.glib)

    def test_invalid_stream_identity_is_rejected(self):
        self.connection.serial = '900001'
        with self.assertRaisesRegex(RuntimeError, 'invalid stream serial'):
            self.session.open()
        self.assertNotIn('OpenPipeWireRemote', self.connection.calls)

    def test_encoder_failure_closes_session_and_fd(self):
        with patch.object(worker, 'native_stack', return_value=(self.gio, self.glib, Mock())), \
             patch.object(worker, 'ScreenCastSession', return_value=self.session), \
             patch.object(worker, 'codec', return_value='x264enc'), \
             patch.object(worker, 'record_pipeline', side_effect=RuntimeError('Encoder failed')):
            with self.assertRaisesRegex(RuntimeError, 'Encoder failed'):
                worker.capture(Mock(), 1)
        self.assertEqual(self.connection.subscriptions, {})
        with self.assertRaises(OSError):
            os.fstat(self.connection.returned_fd)
