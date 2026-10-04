import json
import os
import re
import ssl
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from .node import private_bytes
from .protocol import MAX_ENVELOPE_BYTES, device_name, sign, verify_response


def client_config(path):
    try:
        config = json.loads(private_bytes(path))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('Invalid private node client configuration') from None
    if (not isinstance(config, dict) or set(config) - {'device', 'key', 'url', 'socket', 'ca_certificate'}
            or not {'device', 'key'} <= set(config)):
        raise ValueError('Invalid node client configuration fields')
    device_name(config['device'])
    if not isinstance(config['key'], str) or not re.fullmatch('[a-fA-F0-9]{64}', config['key']):
        raise ValueError('Invalid node client key')
    if bool(config.get('url')) == bool(config.get('socket')):
        raise ValueError('Choose exactly one HTTPS endpoint or private Unix socket')
    if config.get('socket'):
        if not isinstance(config['socket'], str) or config.get('ca_certificate'):
            raise ValueError('Invalid Unix client options')
        path = Path(config['socket'])
        parent, info = path.parent.lstat(), path.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077
                or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('Choose an owner-only Unix socket')
    else:
        url = config['url']
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError('Invalid node HTTPS endpoint')
        endpoint = urlsplit(url)
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment or endpoint.path != '/v1/request'):
            raise ValueError('Node requests require a fixed HTTPS endpoint without inline credentials')
        if 'ca_certificate' in config and not isinstance(config['ca_certificate'], str):
            raise ValueError('Invalid trusted certificate path')
    return config


class SentinelClient:
    def __init__(self, config_path, wall_clock=time.time):
        self.config_path, self.wall_clock = Path(config_path), wall_clock

    async def request(self, action):
        config = client_config(self.config_path)
        key = bytes.fromhex(config['key'])
        envelope = sign(config['device'], action, key, now=self.wall_clock())
        if config.get('socket'):
            connector = aiohttp.UnixConnector(path=config['socket'])
            url = 'http://node/v1/request'
        else:
            context = ssl.create_default_context(cafile=config.get('ca_certificate'))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connector = aiohttp.TCPConnector(ssl=context)
            url = config['url']
        timeout = aiohttp.ClientTimeout(total=5, connect=2, sock_read=2)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as client:
            async with client.post(url, json=envelope, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('Node request was not confirmed; no automatic retry')
                body = bytearray()
                async for chunk in response.content.iter_chunked(1024):
                    body.extend(chunk)
                    if len(body) > MAX_ENVELOPE_BYTES:
                        raise ValueError('Node response exceeded the protocol limit')
                try:
                    data = json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ValueError('Invalid node response') from None
                verified = verify_response(data, envelope, key, now=self.wall_clock())
        return {name: verified[name] for name in ('version', 'action', 'timestamp', 'packet_sent', 'host_state', 'scope')}
