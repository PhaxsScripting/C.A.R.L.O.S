#!/usr/bin/env python3
"""Check private settings and silent speech rates with owned temporary state."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--core-dir',type=Path,default=Path(__file__).resolve().parents[1]/'core')
parser.add_argument('--config',type=Path)
arguments=parser.parse_args()
sys.path.insert(0,str(arguments.core_dir.resolve(strict=True)))
os.environ['PYTHONPATH']=sys.path[0]
from ev.paths import Paths, get_paths
from ev.service import CoreService

async def main():
    host_path=arguments.config or get_paths().config_file
    host_before=host_path.read_bytes()
    host=json.loads(host_before)
    with tempfile.TemporaryDirectory(prefix='carlos-private-delivery-') as directory:
        root=Path(directory)
        core=CoreService(paths=Paths(*(root/name for name in ('config','data','state','cache','ev'))))
        core.config['voice']['tts'].update(host['voice']['tts'])
        writer=None;sequence=0;workers=[]
        try:
            await core.ipc.start()
            reader,writer=await asyncio.open_unix_connection(str(core.paths.socket));await reader.readline()
            async def call(payload):
                nonlocal sequence
                sequence+=1;identifier='owned-delivery-'+str(sequence)
                writer.write((json.dumps({'id':identifier,'type':'personality.update','payload':payload})+'\n').encode())
                await writer.drain()
                async with asyncio.timeout(4):
                    while True:
                        reply=json.loads(await reader.readline())
                        if reply.get('id')==identifier:return reply
            slow=await call({'humor':'off','sarcasm':'light','name_usage':'rare','speaking_rate':0.85})
            assert slow['type']=='response'
            assert core.voice.tts.available[0] and core.voice.tts.selected=='piper'
            first=await core.voice.tts.synthesize('This is an owned silent speaking rate check for Carlos.')
            worker=core.voice.tts.piper._worker
            assert worker and worker.returncode is None;workers.append(worker)
            cli=await asyncio.create_subprocess_exec(sys.executable,'-m','ev.cli','personality','speaking_rate','1.3',
                                                    env=dict(os.environ,XDG_RUNTIME_DIR=str(root)),
                                                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(5):
                    output,error=await cli.communicate()
            except BaseException:
                if cli.returncode is None:
                    cli.kill()
                await cli.wait()
                raise
            assert cli.returncode==0,error.decode()
            fast=json.loads(output)
            assert fast['type']=='response' and fast['payload']['personality']['speaking_rate']==1.3
            second=await core.voice.tts.synthesize('This is an owned silent speaking rate check for Carlos.')
            assert first.engine==second.engine=='piper' and len(first.pcm)>len(second.pcm)>0
            assert core.voice.tts.piper._worker is worker
            persisted=json.loads(core.paths.config_file.read_text())
            assert persisted['voice']['tts']['speaking_rate']==1.3
            assert persisted['personality']['speaking_rate']==1.3
            await core.voice.tts.close();assert worker.returncode is not None
            refused=[]
            for mode in ('PRIVATE SESSION','GUEST'):
                await core.privacy.set_mode(mode)
                before=core.paths.config_file.read_bytes()
                reply=await call({'tone':'calm','voice_expressiveness':0.8})
                assert reply['type']=='error' or (reply['type']=='response' and reply['payload'].get('status')=='denied'),reply
                assert core.paths.config_file.read_bytes()==before
                refused.append(mode)
            assert host_path.read_bytes()==host_before
            duration=lambda audio:len(audio.pcm)/(audio.sample_rate*audio.channels*audio.sample_width)
            print(json.dumps({'scope':'private Core IPC, temporary configuration and actual silent Piper PCM',
                              'slower_duration_seconds':round(duration(first),4),
                              'faster_duration_seconds':round(duration(second),4),
                              'actual_cli_numeric_rate_update':True,'one_persistent_worker_both_rates':True,'owned_worker_reaped':True,
                              'actual_privacy_modes_refused_without_persistence':refused,
                              'host_config_unchanged':True,'playback':False,'microphone':False,'desktop_actions':0}))
        finally:
            if writer:writer.close();await writer.wait_closed()
            await core.ipc.stop();await core.voice.close()
            if hasattr(core.brain.provider,'close'):await core.brain.provider.close()
            core.daily.close();core.memory.close();core.task_journal.close()
            assert all(worker.returncode is not None for worker in workers)
asyncio.run(main())
