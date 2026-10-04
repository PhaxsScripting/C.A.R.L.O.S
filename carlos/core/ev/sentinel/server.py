import asyncio
import json
import os
import sqlite3
import ssl
import stat
from pathlib import Path

from aiohttp import web

from .node import ReferenceNode, private_bytes
from .protocol import MAX_ENVELOPE_BYTES


def application(node):
    async def request(req):
        try:
            data = json.loads(await req.read())
            response = await asyncio.to_thread(node.handle, data)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({'error': 'Request too large'}, status=413)
        except (ValueError, TypeError, KeyError, PermissionError):
            return web.json_response({'error': 'Request rejected'}, status=403)
        except (OSError, sqlite3.Error):
            return web.json_response({'error': 'Node unavailable; dispatch is unconfirmed'}, status=503)
        return web.json_response(response)
    app = web.Application(client_max_size=MAX_ENVELOPE_BYTES)
    app.router.add_post('/v1/request', request)
    return app


async def serve(config, *, socket_path=None, port=None, certificate=None, tls_key=None, allow_wake=False):
    if os.geteuid() == 0:
        raise PermissionError('Run the node as an unprivileged service user')
    if port is not None:
        if type(port) is not int or not 1024 <= port <= 65535 or not certificate or not tls_key or socket_path:
            raise ValueError('TCP serving requires an explicit loopback port, certificate and private TLS key')
        private_bytes(tls_key, 65536)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(certificate, tls_key)
    else:
        if certificate or tls_key:
            raise ValueError('Certificate options require the explicit TCP port')
        path = Path(socket_path or Path(config).with_suffix('.sock'))
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError('The node socket requires an owner-only directory')
        if path.exists() or path.is_symlink():
            raise ValueError('Refusing to replace an existing node socket')
    node = ReferenceNode(config, allow_wake=allow_wake)
    runner = web.AppRunner(application(node), access_log=None, shutdown_timeout=2)
    owned_socket = None
    try:
        await runner.setup()
        if port is None:
            site = web.UnixSite(runner, str(path))
            await site.start()
            os.chmod(path, 0o600)
            info = path.lstat()
            owned_socket = (info.st_dev, info.st_ino)
        else:
            site = web.TCPSite(runner, '127.0.0.1', port, ssl_context=tls)
            await site.start()
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        node.close()
        if owned_socket:
            try:
                info = path.lstat()
                if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == owned_socket:
                    path.unlink()
            except FileNotFoundError:
                pass
