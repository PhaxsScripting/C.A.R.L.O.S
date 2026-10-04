#!/usr/bin/env python3
"""Exercise a disposable reference node; never send a wake packet."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time


def main():
    with tempfile.TemporaryDirectory(prefix='carlos-node-check-') as directory:
        root = Path(directory)
        key = secrets.token_hex(32)
        config = root / 'node.json'
        config.write_text(json.dumps({'devices': {'check': {'key': key, 'capabilities': ['status']}},
                                     'target_mac': '02:02:03:04:05:06', 'broadcast': '192.0.2.255'}))
        config.chmod(0o600)
        socket = root / 'node.sock'
        client = root / 'client.json'
        client.write_text(json.dumps({'device': 'check', 'key': key, 'socket': str(socket)}))
        client.chmod(0o600)
        command = [sys.executable, '-m', 'ev.sentinel']
        result = subprocess.run(command + ['simulate'], capture_output=True, timeout=15, check=True)
        simulated = json.loads(result.stdout)
        assert simulated['network_packets_sent'] == 0 and not simulated['physical_wake_verified']
        server = subprocess.Popen(command + ['serve', '--config', str(config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not socket.exists():
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise AssertionError('Owned node did not start')
                time.sleep(.02)
            result = subprocess.run(command + ['request', '--client-config', str(client)],
                                    capture_output=True, timeout=10, check=True)
            response = json.loads(result.stdout)
            assert response['host_state'] == 'UNKNOWN' and not response['packet_sent']
            assert key not in result.stdout.decode() and 'signature' not in response
            assert socket.stat().st_mode & 0o777 == 0o600
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait()
                    raise AssertionError('Owned node did not stop gracefully')
        assert server.returncode == 0 and not socket.exists()
        print(json.dumps({'scope': 'owned CLI processes and private Unix socket',
                          'signed_status_checked': True, 'server_joined': True,
                          'socket_removed': True, 'network_wake_packets_sent': 0,
                          'physical_wake_verified': False}))


if __name__ == '__main__':
    main()
