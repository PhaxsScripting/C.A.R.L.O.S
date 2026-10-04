import argparse
import asyncio
import json
import secrets
import signal
import sqlite3
import tempfile
from pathlib import Path

from .client import SentinelClient
from .node import ReferenceNode
from .protocol import magic_packet, sign, verify_response
from .server import serve
from aiohttp import ClientError


async def run_server(args):
    task = asyncio.create_task(serve(args.config, socket_path=args.socket, port=args.port,
                                    certificate=args.certificate, tls_key=args.tls_key,
                                    allow_wake=args.allow_wake))
    loop = asyncio.get_running_loop()
    for number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(number, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for number in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(number)


def simulate():
    with tempfile.TemporaryDirectory(prefix='carlos-sentinel-reference-') as directory:
        path = Path(directory)/'node.json'
        phone, host = secrets.token_bytes(32), secrets.token_bytes(32)
        config = {'devices': {'phone': {'key': phone.hex(), 'capabilities': ['status', 'wake']},
                              'host': {'key': host.hex(), 'capabilities': ['heartbeat']}},
                  'target_mac': '02:02:03:04:05:06', 'broadcast': '192.0.2.255', 'host_device': 'host'}
        path.write_text(json.dumps(config)); path.chmod(0o600)
        now = [100.]
        packets = []
        def memory_sender(mac, broadcast):
            packets.append(magic_packet(mac))
            return {'packet_sent': True}
        node = ReferenceNode(path, allow_wake=True, sender=memory_sender, clock=lambda:now[0], wall_clock=lambda:now[0])
        try:
            request = sign('phone', 'wake', phone, now=now[0])
            first = verify_response(node.handle(request), request, phone, now=now[0])
            assert first['host_state'] == 'UNKNOWN'
            now[0] += 2
            heartbeat = sign('host', 'heartbeat', host, now=now[0])
            observed = verify_response(node.handle(heartbeat), heartbeat, host, now=now[0])
            assert observed['host_state'] == 'ONLINE'
            now[0] += 1
            status = sign('phone', 'status', phone, now=now[0])
            verify_response(node.handle(status), status, phone, now=now[0])
            node.close()
            node = ReferenceNode(path, clock=lambda:now[0], wall_clock=lambda:now[0])
            now[0] += 1
            try:
                node.handle(status)
            except ValueError:
                replay_refused = True
            else:
                raise AssertionError('Persistent replay was accepted')
            fresh = sign('phone', 'status', phone, now=now[0])
            restarted = verify_response(node.handle(fresh), fresh, phone, now=now[0])
            assert restarted['host_state'] == 'UNKNOWN'
            return {'scope': 'in-memory wake dispatch and synthetic authenticated heartbeat, with real private SQLite',
                    'protocol_version': 1, 'simulated_packets': len(packets), 'simulated_packet_bytes': len(packets[0]),
                    'network_packets_sent': 0, 'persistent_replay_refused': replay_refused,
                    'restart_discards_live_host_claim': True, 'physical_wake_verified': False,
                    'microphone_opened': False, 'models_started': False, 'private_memory_replicated': False}
        finally:
            node.close()


def main():
    parser = argparse.ArgumentParser(description='Carlos Sentinel reference node; independent hardware is required for real wake.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('simulate')
    request = commands.add_parser('request')
    request.add_argument('--client-config', type=Path, required=True)
    request.add_argument('--action', choices=['status', 'wake', 'heartbeat'], default='status')
    server = commands.add_parser('serve')
    server.add_argument('--config', type=Path, required=True)
    server.add_argument('--socket', type=Path)
    server.add_argument('--port', type=int)
    server.add_argument('--certificate', type=Path)
    server.add_argument('--tls-key', type=Path)
    server.add_argument('--allow-wake', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'simulate':
            print(json.dumps(simulate()))
        elif args.command == 'request':
            print(json.dumps(asyncio.run(SentinelClient(args.client_config).request(args.action))))
        else:
            asyncio.run(run_server(args))
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, sqlite3.Error, ClientError) as error:
        parser.exit(1, f'Node setup failed ({type(error).__name__}); check private configuration and transport.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
