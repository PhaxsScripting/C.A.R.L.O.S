import copy
import hashlib
import hmac
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from ev.sentinel.protocol import (Verifier, canonical, checked_key, clock_seconds, devices_config,
                                 magic_packet, send_wake, sign, signed_response, verify_response)


class SentinelProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.key=b'k'*32
        self.devices={'phone':{'key':self.key.hex(),'capabilities':['wake','status']},
                      'host':{'key':(b'h'*32).hex(),'capabilities':['heartbeat']}}
        self.verifier=Verifier(self.root/'nonces.db',self.devices)

    def tearDown(self):
        self.verifier.close()
        self.temporary.cleanup()

    def signed_variant(self, **changes):
        envelope=sign('phone','status',self.key,now=100)
        envelope.update(changes)
        fields={key:value for key,value in envelope.items() if key!='signature'}
        envelope['signature']=hmac.new(self.key,canonical(fields),hashlib.sha256).hexdigest()
        return envelope

    def test_existing_request_canonical_signature_remains_compatible(self):
        envelope=sign('phone','wake',self.key,now=100)
        fields={key:value for key,value in envelope.items() if key!='signature'}
        self.assertEqual(envelope['signature'],hmac.new(self.key,canonical(fields),hashlib.sha256).hexdigest())
        self.assertEqual(self.verifier.verify(envelope,now=100),'wake')
        self.assertEqual((self.root/'nonces.db').stat().st_mode & 0o777,0o600)

    def test_replay_is_refused_after_reopening_actual_sqlite_store(self):
        envelope=sign('phone','status',self.key,now=100)
        self.verifier.verify(envelope,now=100)
        self.verifier.close()
        self.verifier=Verifier(self.root/'nonces.db',self.devices)
        with self.assertRaisesRegex(ValueError,'Replay'):self.verifier.verify(envelope,now=101)

    def test_exact_version_timestamp_action_nonce_and_fields_are_required(self):
        variants=({'version':True},{'version':2},{'timestamp':True},{'timestamp':100.0},
                  {'timestamp':69},{'timestamp':131},{'device':[]},{'device':'other'},
                  {'action':[]},{'action':'shutdown'},{'nonce':'wrong'},{'nonce':False},
                  {'destination':'other-host'})
        for changes in variants:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.verifier.verify(self.signed_variant(**changes),now=100)
        envelope=sign('phone','status',self.key,now=100)
        envelope.pop('signature')
        with self.assertRaises(ValueError):self.verifier.verify(envelope,now=100)

    def test_tampering_wrong_key_revocation_and_capability_changes_are_live(self):
        envelope=sign('phone','status',b'x'*32,now=100)
        with self.assertRaises(ValueError):self.verifier.verify(envelope,now=100)
        envelope=sign('phone','status',self.key,now=100);envelope['timestamp']=101
        with self.assertRaises(ValueError):self.verifier.verify(envelope,now=100)
        for mutation in ('revoked','capabilities','key'):
            before=copy.deepcopy(self.devices['phone'])
            if mutation=='revoked':self.devices['phone']['revoked']=True
            elif mutation=='capabilities':self.devices['phone']['capabilities']=['wake']
            else:self.devices['phone']['key']=(b'z'*32).hex()
            with self.assertRaises(ValueError):self.verifier.verify(sign('phone','status',self.key,now=100),now=100)
            self.devices['phone']=before
        self.assertEqual(self.verifier.verify(sign('phone','status',self.key,now=100),now=100),'status')

    def test_per_client_and_global_wake_limits_do_not_accept_more_packets(self):
        self.verifier.verify(sign('phone','wake',self.key,now=100),now=100)
        with self.assertRaises(ValueError):self.verifier.verify(sign('phone','wake',self.key,now=101),now=101)
        other=b'o'*32
        self.devices['other']={'key':other.hex(),'capabilities':['wake']}
        with self.assertRaises(ValueError):self.verifier.verify(sign('other','wake',other,now=101),now=101)
        self.assertEqual(self.verifier.verify(sign('other','wake',other,now=102),now=102),'wake')
        self.assertEqual(self.verifier.verify(sign('phone','wake',self.key,now=110),now=110),'wake')

    def test_status_rate_limit_and_storage_pressure_fail_closed_without_eviction(self):
        self.verifier.close();self.verifier=Verifier(self.root/'nonces.db',self.devices,max_nonces=2)
        first=sign('phone','status',self.key,now=100)
        self.verifier.verify(first,now=100)
        with self.assertRaisesRegex(ValueError,'rate limit'):self.verifier.verify(sign('phone','status',self.key,now=100),now=100)
        self.verifier.verify(sign('phone','status',self.key,now=101),now=101)
        with self.assertRaisesRegex(ValueError,'storage is full'):self.verifier.verify(sign('phone','status',self.key,now=102),now=102)
        self.assertEqual(self.verifier.db.execute('SELECT count(*) FROM nonces').fetchone()[0],2)
        with self.assertRaisesRegex(ValueError,'Replay'):self.verifier.verify(first,now=102)
        self.assertEqual(self.verifier.verify(sign('phone','status',self.key,now=222),now=222),'status')
        self.assertEqual(self.verifier.db.execute('SELECT count(*) FROM nonces').fetchone()[0],1)

    def test_signed_heartbeat_requires_its_own_explicit_capability(self):
        with self.assertRaises(ValueError):self.verifier.verify(sign('phone','heartbeat',self.key,now=100),now=100)
        self.assertEqual(self.verifier.verify(sign('host','heartbeat',b'h'*32,now=100),now=100),'heartbeat')

    def test_wall_clock_rollback_is_refused_without_writing_more_state(self):
        self.verifier.verify(sign('phone','status',self.key,now=100),now=100)
        with self.assertRaisesRegex(ValueError,'backwards'):self.verifier.verify(sign('phone','status',self.key,now=99),now=99)
        self.assertEqual(self.verifier.db.execute('SELECT count(*) FROM nonces').fetchone()[0],1)

    def test_atomic_replay_check_with_two_real_database_connections(self):
        other=Verifier(self.root/'nonces.db',self.devices)
        self.addCleanup(other.close)
        envelope=sign('phone','status',self.key,now=100)
        barrier=threading.Barrier(2)
        def accept(verifier):
            barrier.wait()
            try:verifier.verify(envelope,now=100);return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            replies=list(pool.map(accept,(self.verifier,other)))
        self.assertEqual(sorted(replies),[False,True])
        self.assertEqual(self.verifier.db.execute('SELECT count(*) FROM nonces').fetchone()[0],1)

    def test_database_links_public_permissions_and_nonprivate_parent_are_refused(self):
        target=self.root/'target';target.write_bytes(b'');target.chmod(0o600)
        link=self.root/'link';link.symlink_to(target)
        with self.assertRaises(OSError):Verifier(link,self.devices)
        hardlink=self.root/'hardlink';os.link(target,hardlink)
        with self.assertRaises(ValueError):Verifier(hardlink,self.devices)
        public=self.root/'public';public.write_bytes(b'');public.chmod(0o644)
        with self.assertRaises(ValueError):Verifier(public,self.devices)
        folder=self.root/'public-dir';folder.mkdir(mode=0o755);folder.chmod(0o755)
        with self.assertRaises(ValueError):Verifier(folder/'state',self.devices)

    def test_invalid_keys_configs_actions_and_clocks_are_refused(self):
        for value in (None,b'x'*31,b'x'*33,'k'*32):
            with self.assertRaises(ValueError):checked_key(value)
        for value in (True,float('inf'),float('nan'),-1,10**400):
            with self.assertRaises(ValueError):clock_seconds(value)
        self.assertEqual(clock_seconds(100.4),100)
        for devices in ({},{'bad name':self.devices['phone']}, {'phone':{**self.devices['phone'],'revoked':1}},
                        {'phone':{**self.devices['phone'],'capabilities':['wake','wake']}},
                        {'phone':{**self.devices['phone'],'capabilities':[True]}},
                        {'phone':{**self.devices['phone'],'key':'secret'}}):
            with self.assertRaises(ValueError):devices_config(devices)
        for action in ('shutdown',None,[]):
            with self.assertRaises(ValueError):sign('phone',action,self.key,now=100)

    def test_responses_are_bound_signed_fresh_and_not_proof_of_waking(self):
        request=sign('phone','wake',self.key,now=100)
        response=signed_response(request,self.key,packet_sent=True,now=100)
        verified=verify_response(response,request,self.key,now=100)
        self.assertEqual(verified['host_state'],'UNKNOWN')
        self.assertTrue(verified['packet_sent'])
        with self.assertRaises(ValueError):verify_response(response,sign('phone','wake',self.key,now=100),self.key,now=100)
        with self.assertRaises(ValueError):verify_response(response,request,b'x'*32,now=100)
        with self.assertRaises(ValueError):verify_response(response,request,self.key,now=131)
        for key,value in (('version',True),('packet_sent',1),('host_state','SLEEPING'),('host_state',[]),('timestamp',True),('signature','wrong')):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):verify_response({**response,key:value},request,self.key,now=100)
        with self.assertRaises(ValueError):verify_response({'version':1,'sentinel':'ONLINE'},request,self.key,now=100)

    def test_wake_packet_format_and_configured_target_only(self):
        packet=magic_packet('02:02:03:04:05:06')
        self.assertEqual(packet,b'\xff'*6+bytes.fromhex('020203040506')*16)
        for mac in ('00:00:00:00:00:00','01:02:03:04:05:06','host; command',None):
            with self.assertRaises(ValueError):magic_packet(mac)
        connection=MagicMock();connection.sendto.return_value=102
        with patch('ev.sentinel.protocol.socket.socket') as socket_factory:
            socket_factory.return_value.__enter__.return_value=connection
            result=send_wake('02:02:03:04:05:06','192.0.2.255')
        connection.sendto.assert_called_once_with(packet,('192.0.2.255',9))
        self.assertTrue(result['packet_sent'])
        self.assertEqual(result['host_state'],'UNKNOWN')
        connection.sendto.return_value=101
        with patch('ev.sentinel.protocol.socket.socket') as socket_factory:
            socket_factory.return_value.__enter__.return_value=connection
            with self.assertRaises(OSError):send_wake('02:02:03:04:05:06','192.0.2.255')


if __name__=='__main__':unittest.main()
