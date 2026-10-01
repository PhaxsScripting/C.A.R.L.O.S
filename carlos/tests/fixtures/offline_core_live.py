"""Actual IPC and local tools in a disposable Linux network namespace."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta

from ev.ai.carlos_router import CarlosRouter
from ev.ai.offline import OfflineProvider
from ev.ai.openai_responses import OpenAIResponsesProvider
from ev.paths import Paths
from ev.service import CoreService


async def main():
    assert os.readlink('/proc/self/ns/net') != os.environ['CARLOS_TEST_PARENT_NETNS']
    assert not Path('/proc/net/route').read_text().strip().splitlines()[1:]
    with socket.socket() as probe:
        probe.settimeout(.2)
        assert probe.connect_ex(('192.0.2.1',443)) != 0
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        core=CoreService(paths=Paths(*(root/name for name in ['config','data','state','cache','run'])))
        core.config['security']['allowed_roots']=[str(root)]
        core.config['assistant']['speak_responses']=False
        os.environ['CARLOS_OFFLINE_FIXTURE_KEY']='offline-fixture-not-a-credential'
        cloud=OpenAIResponsesProvider({'model':'offline-fixture','api_key_env':'CARLOS_OFFLINE_FIXTURE_KEY',
            'base_url':'http://192.0.2.1:12345/v1','timeout_seconds':1})
        core.brain.provider=CarlosRouter(OfflineProvider(),cloud,lambda:core.privacy.mode)
        results=[]
        await core.ipc.start()
        reader,writer=await asyncio.open_unix_connection(str(core.paths.socket))
        await reader.readline()
        async def call(kind,payload=None):
            identifier=uuid.uuid4().hex
            writer.write((json.dumps({'type':kind,'id':identifier,'payload':payload or {}})+'\n').encode())
            await writer.drain()
            while True:
                line=await asyncio.wait_for(reader.readline(),8)
                assert line,'IPC disconnected'
                message=json.loads(line)
                if message.get('id')==identifier:
                    assert message['type']!='error',message
                    return message['payload']
        try:
            for text in ['Hello Carlos','What is my CPU usage?','How much memory am I using?']:
                start=time.monotonic();result=await call('command.submit',{'text':text})
                assert result['status']=='completed',result
                results.append({'operation':text,'status':result['status'],'elapsed_ms':round((time.monotonic()-start)*1000,1)})
            file=root/'offline.txt'
            result=await call('tool.call',{'name':'files.text.create','arguments':{'path':str(file),'content':'offline file fixture\n'}})
            assert result['status']=='completed',result
            assert file.read_text()=='offline file fixture\n'
            result=await call('tool.call',{'name':'memory.remember','arguments':{'content':'Offline fixture memory'}})
            assert result['status']=='completed',result
            assert 'Offline fixture memory' in json.dumps(core.memory.list_memories())
            results.append({'operation':'files and memory','status':'verified by local readback'})
            result=await call('tool.call',{'name':'memory.project.select','arguments':{'project':str(root)}})
            assert result['status']=='completed',result
            core.memory.add_conversation('history-fixture','user','offline_history_canary',project=str(root))
            core.memory._connection.execute('UPDATE conversations SET created_at=? WHERE correlation_id=?',
                ((datetime.now(UTC)-timedelta(seconds=2)).isoformat(),'history-fixture'))
            core.memory._connection.commit()
            result=await call('tool.call',{'name':'memory.recall','arguments':{'period':'today'}})
            assert result['status']=='completed',result
            assert 'offline_history_canary' in json.dumps(result)
            assert result['result']['project']==str(root)
            assert result['result']['live_state_verified'] is False
            result=await call('command.submit',{'text':'What was I doing today?','speak':False})
            assert result['status']=='completed' and result['actions_executed']==0,result
            assert 'offline_history_canary' in result['response']
            result=await call('tool.call',{'name':'memory.project.select','arguments':{'project':''}})
            assert result['status']=='completed',result
            results.append({'operation':'scoped dated history and natural request','status':'actual offline IPC; no mutations'})
            result=await call('carlos.privacy.set',{'mode':'LOCAL ONLY'})
            assert result['mode']=='LOCAL ONLY'
            result=await call('command.submit',{'text':'use cloud: Describe a comet'})
            assert result['status']=='offline',result
            assert cloud.transport_status()['state']=='UNTESTED','Local-only policy contacted cloud'
            results.append({'operation':'local-only cloud refusal','status':'no transport request'})
            await call('carlos.privacy.set',{'mode':'NORMAL'})
            result=await call('command.submit',{'text':'use cloud: Describe a comet'})
            assert result['status']=='offline',result
            assert cloud.transport_status()['state']!='UNTESTED'
            assert (await call('health'))['state']=='DORMANT'
            result=await call('command.submit',{'text':'What is my CPU usage?'})
            assert result['status']=='completed',result
            results.append({'operation':'cloud network failure then local command','status':'recovered without replay'})
        finally:
            writer.close();await writer.wait_closed();await core.ipc.stop()
            await core.voice.close();await core.brain.provider.close()
            core.daily.close();core.task_journal.close();core.memory.close()
        print(json.dumps({'network_namespace':'isolated; no route','checks':results},indent=2))


asyncio.run(main())
