import asyncio
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web

from ev.sentinel.client import SentinelClient, client_config
from ev.sentinel.__main__ import simulate
from ev.sentinel.node import ReferenceNode, load_config
from ev.sentinel.protocol import sign, verify_response
from ev.sentinel.server import application, serve


class NodeFixture:
    def setup_node(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.config_path=self.root/'node.json'
        self.phone,self.host=secrets.token_bytes(32),secrets.token_bytes(32)
        self.config={'devices':{'phone':{'key':self.phone.hex(),'capabilities':['status','wake']},
                                'host':{'key':self.host.hex(),'capabilities':['heartbeat']}},
                     'target_mac':'02:02:03:04:05:06','broadcast':'192.0.2.255','host_device':'host'}
        self.write_config()
        self.now=[100.]
        self.node=ReferenceNode(self.config_path,clock=lambda:self.now[0],wall_clock=lambda:self.now[0])

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config));self.config_path.chmod(0o600)

    def request(self,action='status',device='phone'):
        return sign(device,action,self.phone if device=='phone' else self.host,now=self.now[0])

    def handle(self,action='status',device='phone'):
        request=self.request(action,device)
        return verify_response(self.node.handle(request),request,self.phone if device=='phone' else self.host,now=self.now[0])

    def close_node(self):
        self.node.close();self.temporary.cleanup()


class SentinelNodeTests(NodeFixture,unittest.TestCase):
    def setUp(self):self.setup_node()
    def tearDown(self):self.close_node()

    def test_wake_is_disabled_by_default_and_fixed_dispatch_requires_explicit_option(self):
        with patch('ev.sentinel.protocol.socket.socket') as sockets:
            with self.assertRaises(PermissionError):self.handle('wake')
            sockets.assert_not_called()
        self.node.close();calls=[]
        def owned_sender(mac,broadcast):
            calls.append((mac,broadcast));return {'packet_sent':True}
        self.node=ReferenceNode(self.config_path,allow_wake=True,sender=owned_sender,
                                clock=lambda:self.now[0],wall_clock=lambda:self.now[0])
        self.now[0]+=10
        response=self.handle('wake')
        self.assertTrue(response['packet_sent'])
        self.assertEqual(response['host_state'],'UNKNOWN')
        self.assertEqual(calls,[('02:02:03:04:05:06','192.0.2.255')])
        with self.assertRaises(ValueError):self.node.handle({**self.request('wake'),'target':'another computer'})
        self.assertEqual(len(calls),1)

    def test_online_requires_the_exact_recent_authenticated_host_heartbeat(self):
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')
        with self.assertRaises(ValueError):self.handle('heartbeat')
        self.assertEqual(self.handle('heartbeat','host')['host_state'],'ONLINE')
        self.now[0]+=1
        self.assertEqual(self.handle()['host_state'],'ONLINE')
        self.now[0]+=16
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')
        self.assertEqual(self.handle('heartbeat','host')['host_state'],'ONLINE')
        self.node.close();self.node=ReferenceNode(self.config_path,clock=lambda:self.now[0],wall_clock=lambda:self.now[0])
        self.now[0]+=1
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')

    def test_revocation_key_rotation_and_bad_config_never_keep_an_online_claim(self):
        self.handle('heartbeat','host')
        self.config['devices']['host']['revoked']=True;self.write_config()
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')
        self.config['devices']['host']['revoked']=False
        self.config['devices']['host']['key']=secrets.token_bytes(32).hex();self.write_config()
        self.now[0]+=1
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')
        self.config_path.write_text('{bad json')
        self.now[0]+=1
        with self.assertRaises(ValueError):self.handle()

    def test_private_config_permissions_links_duplicate_keys_and_roles_are_enforced(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(ValueError):load_config(self.config_path)
        self.write_config()
        link=self.root/'link';link.symlink_to(self.config_path)
        with self.assertRaises(OSError):load_config(link)
        self.config['devices']['host']['key']=self.phone.hex();self.write_config()
        with self.assertRaises(ValueError):load_config(self.config_path)
        self.config['devices']['host']['key']=self.host.hex()
        self.config['host_device']='phone';self.write_config()
        with self.assertRaises(ValueError):load_config(self.config_path)

    def test_dispatch_failure_is_unconfirmed_and_never_replayed_automatically(self):
        self.node.close()
        calls=[]
        def fail(mac,broadcast):
            calls.append(mac);raise OSError('dispatch is unconfirmed')
        self.node=ReferenceNode(self.config_path,allow_wake=True,sender=fail,
                                clock=lambda:self.now[0],wall_clock=lambda:self.now[0])
        request=self.request('wake')
        with self.assertRaises(OSError):self.node.handle(request)
        self.now[0]+=1
        with self.assertRaises(ValueError):self.node.handle(request)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.handle()['host_state'],'UNKNOWN')

    def test_reference_simulation_opens_no_network_socket_and_makes_no_physical_claim(self):
        with patch('ev.sentinel.protocol.socket.socket',side_effect=AssertionError('network socket opened')):
            result=simulate()
        self.assertEqual(result['network_packets_sent'],0)
        self.assertEqual(result['simulated_packet_bytes'],102)
        self.assertFalse(result['physical_wake_verified'])
        self.assertTrue(result['persistent_replay_refused'])
        self.assertTrue(result['restart_discards_live_host_claim'])


class SentinelTransportTests(NodeFixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.setup_node()
    async def asyncTearDown(self):self.close_node()

    async def test_actual_private_unix_http_signatures_rejection_and_socket_cleanup(self):
        socket_path=self.root/'node.sock'
        task=asyncio.create_task(serve(self.config_path,socket_path=socket_path))
        try:
            async with asyncio.timeout(3):
                while not socket_path.exists():
                    if task.done():task.result()
                    await asyncio.sleep(.01)
            self.assertEqual(socket_path.stat().st_mode & 0o777,0o600)
            request=sign('phone','status',self.phone)
            async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket_path))) as client:
                async with client.post('http://node/v1/request',json=request) as reply:
                    self.assertEqual(reply.status,200)
                    data=verify_response(await reply.json(),request,self.phone)
                    self.assertEqual(data['host_state'],'UNKNOWN')
                async with client.post('http://node/v1/request',json=request) as reply:
                    self.assertEqual(reply.status,403)
                    self.assertNotIn(self.phone.hex(),await reply.text())
                async with client.post('http://node/v1/request',data=b'x'*3000) as reply:
                    self.assertEqual(reply.status,413)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(socket_path.exists())

    async def test_actual_unix_client_reads_private_config_and_returns_no_keys_or_nonces(self):
        socket_path=self.root/'client-node.sock'
        task=asyncio.create_task(serve(self.config_path,socket_path=socket_path))
        try:
            async with asyncio.timeout(3):
                while not socket_path.exists():
                    if task.done():task.result()
                    await asyncio.sleep(.01)
            config=self.root/'client.json'
            config.write_text(json.dumps({'device':'phone','key':self.phone.hex(),'socket':str(socket_path)}));config.chmod(0o600)
            result=await SentinelClient(config).request('status')
            self.assertEqual(result['host_state'],'UNKNOWN')
            self.assertFalse(result['packet_sent'])
            self.assertNotIn('signature',result)
            self.assertNotIn('request_nonce',result)
            self.assertNotIn(self.phone.hex(),json.dumps(result))
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(socket_path.exists())

    async def test_client_refuses_plaintext_redirects_and_unsigned_or_oversized_results(self):
        config=self.root/'client.json'
        config.write_text(json.dumps({'device':'phone','key':self.phone.hex(),'url':'http://127.0.0.1/v1/request'}));config.chmod(0o600)
        with self.assertRaises(ValueError):client_config(config)
        socket_path=self.root/'bad-server.sock'
        mode=['unsigned'];calls=[]
        async def answer(request):
            calls.append(await request.read())
            if mode[0]=='redirect':return web.Response(status=307,headers={'Location':'http://elsewhere.invalid/'})
            if mode[0]=='large':return web.Response(body=b'x'*3000)
            return web.json_response({'version':1,'host_state':'ONLINE'})
        app=web.Application();app.router.add_post('/v1/request',answer)
        runner=web.AppRunner(app,access_log=None);await runner.setup()
        site=web.UnixSite(runner,str(socket_path));await site.start();socket_path.chmod(0o600)
        config.write_text(json.dumps({'device':'phone','key':self.phone.hex(),'socket':str(socket_path)}))
        try:
            for value in ('unsigned','large','redirect'):
                mode[0]=value
                with self.assertRaises(ValueError):await SentinelClient(config).request('status')
            self.assertEqual(len(calls),3)
        finally:
            await runner.cleanup();socket_path.unlink(missing_ok=True)

    async def test_existing_socket_plaintext_tcp_and_unsupported_options_are_refused(self):
        existing=self.root/'node.sock';existing.write_text('keep me')
        with self.assertRaises(ValueError):await serve(self.config_path,socket_path=existing)
        self.assertEqual(existing.read_text(),'keep me')
        with self.assertRaises(ValueError):await serve(self.config_path,port=8788)
        with self.assertRaises(ValueError):await serve(self.config_path,certificate=self.root/'no-cert')

    async def test_actual_loopback_tls_requires_trusted_certificate_and_authenticates_response(self):
        certificate,tls_key=self.root/'cert.pem',self.root/'tls-key.pem'
        process=await asyncio.create_subprocess_exec('openssl','req','-x509','-newkey','rsa:2048','-nodes',
            '-keyout',str(tls_key),'-out',str(certificate),'-days','1','-subj','/CN=localhost',
            '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost',
            stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(),10)
            self.assertEqual(process.returncode,0)
        finally:
            if process.returncode is None:process.kill();await process.wait()
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(certificate,tls_key)
        runner=web.AppRunner(application(self.node),access_log=None)
        await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0,ssl_context=tls)
        await site.start()
        port=site._server.sockets[0].getsockname()[1]
        url=f'https://127.0.0.1:{port}/v1/request'
        try:
            request=self.request()
            context=ssl.create_default_context(cafile=str(certificate))
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=context)) as client:
                async with client.post(url,json=request) as reply:
                    self.assertEqual(reply.status,200)
                    verified=verify_response(await reply.json(),request,self.phone,now=self.now[0])
                    self.assertFalse(verified['packet_sent'])
                    self.assertEqual(verified['host_state'],'UNKNOWN')
            client_config_path=self.root/'tls-client.json'
            client_config_path.write_text(json.dumps({'device':'phone','key':self.phone.hex(),'url':url,
                                                       'ca_certificate':str(certificate)}));client_config_path.chmod(0o600)
            self.now[0]+=1
            actual=await SentinelClient(client_config_path,wall_clock=lambda:self.now[0]).request('status')
            self.assertEqual(actual['host_state'],'UNKNOWN')
            async with aiohttp.ClientSession() as untrusted:
                with self.assertRaises(aiohttp.ClientConnectorCertificateError):await untrusted.post(url,json=request)
        finally:
            await runner.cleanup()


if __name__=='__main__':unittest.main()
