#!/usr/bin/env python3
"""Silent transport check against an already running local Carlos Core."""

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import time
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from ev.cli import request
async def call(kind,payload):
    response=await request(kind,payload)
    require(response['type'] == 'response', f'{kind} did not return a response')
    return response['payload']
def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def main():
    health = await call('health', {})
    require(health.get('ok') and health.get('state') == 'DORMANT', 'Use an inactive, running Core for this check')
    start=time.monotonic()
    synthesized=await call('carlos.voice.synthesize',{'text':'Carlos, what is my CPU usage?'})
    require(synthesized.get('status') == 'synthesized' and synthesized.get('local_only')
            and synthesized.get('audio_retained') is False, 'Local speech synthesis was unavailable or busy')
    synthesis_ms=(time.monotonic()-start)*1000
    decoded=base64.b64decode(synthesized['audio'],validate=True)
    with wave.open(io.BytesIO(decoded),'rb') as audio:
        rate,channels,width=audio.getframerate(),audio.getnchannels(),audio.getsampwidth()
        pcm=audio.readframes(audio.getnframes())
    require(channels == 1 and width == 2, 'Expected mono 16-bit speech audio')
    process=await asyncio.create_subprocess_exec('ffmpeg','-hide_banner','-loglevel','error',
                '-f','s16le','-ar',str(rate),'-ac','1','-i','pipe:0','-ar','16000','-f','s16le','pipe:1',
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        pcm,error=await asyncio.wait_for(process.communicate(pcm),5)
        require(process.returncode == 0, 'Speech resampling did not finish successfully')
    finally:
        if process.returncode is None:process.kill();await process.wait()
    start=time.monotonic()
    transcribed=await call('carlos.voice.transcribe',{'audio':base64.b64encode(pcm).decode()})
    require(transcribed.get('status') == 'transcribed' and transcribed.get('local_only')
            and transcribed.get('audio_retained') is False, 'Local speech transcription was unavailable or busy')
    recognition_ms=(time.monotonic()-start)*1000
    require(re.fullmatch(r'what\s+is\s+my\s+cpu\s+usage[?.!]*', transcribed['text'].strip(), re.I),
            'Recognition did not match the fixed read-only query; no command submitted')
    start=time.monotonic()
    command=await call('command.submit',{'text':transcribed['text'],'speak':False})
    require(command.get('status') == 'completed' and command.get('execution_status') == 'ANSWERED',
            'The fixed local query did not complete')
    tools=[s['tool'] for s in command['tool_receipts']]
    require(tools == ['system.get_cpu_usage'], 'The query did not use the expected read-only tool')
    report={'scope':'Installed local Piper-to-whisper.cpp transport and deterministic read-only command; synthetic PCM, no microphone or speaker acceptance',
            'synthesis_request_ms':round(synthesis_ms,3),'transcription_request_ms':round(recognition_ms,3),
            'command_request_ms':round((time.monotonic()-start)*1000,3),
            'sample_duration_seconds':round(len(pcm)/32000,3),'normalized_text':transcribed['text'],
            'tools':tools,'local_only':True,'audio_played':False,'microphone_configuration_changed':False,
            'audio_saved':False,'acoustic_acceptance':False,'resampler_reaped':True,'model_warmup_scope':'Current worker state; fixed generated phrase, not a cold-load or human-speech benchmark'}
    print(json.dumps(report))
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Run silent synthesis/transcription and one read-only CPU query')
    if not parser.parse_args().run:
        parser.print_help()
        raise SystemExit(0)
    asyncio.run(main())
