#!/usr/bin/env python3
"""Silent local-model acceptance in a disposable Linux network namespace."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
os.environ['PYTHONPATH'] = sys.path[0]

from ev.config import DEFAULT_CONFIG, _merge
from ev.ai.local_llama import LocalLlamaProvider
from ev.voice.neural_vad import NeuralVadWorker
from ev.voice.speech_wake import SpeechWakeFallback
from ev.voice.stt import WhisperCppAdapter
from ev.voice.tts import TtsRouter
from ev.voice.wake import WakeWordWorker


async def resample(audio):
    process = await asyncio.create_subprocess_exec(
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 's16le',
        '-ar', str(audio.sample_rate), '-ac', '1', '-i', 'pipe:0',
        '-ar', '16000', '-f', 's16le', 'pipe:1',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    pcm, error = await process.communicate(audio.pcm)
    if process.returncode:
        raise RuntimeError(error.decode(errors='replace'))
    return pcm


async def keyword_check(config, pcm):
    matches = []
    worker = WakeWordWorker(config)

    async def detected(message):
        matches.append(message['keyword'])

    started = time.monotonic()
    try:
        await worker.start(detected, lambda *_: None)
        sample = bytes(16000) + pcm + bytes(64000)
        sample += bytes((-len(sample)) % 32000)
        for offset in range(0, len(sample), 3200):
            await worker.feed(sample[offset:offset + 3200])
        deadline = time.monotonic() + 15
        while worker.processed_bytes < len(sample):
            if time.monotonic() > deadline or not worker.listener_ready:
                raise RuntimeError('Keyword worker did not consume the sample')
            await asyncio.sleep(.05)
        return {'matched': bool(matches), 'keywords': matches,
                'elapsed_ms': round((time.monotonic() - started) * 1000)}
    finally:
        await worker.stop()


async def speech_check(vad, stt, pcm, probability, verify):
    matches = []

    async def detected(message):
        matches.append({'keyword': message['keyword'],
                        'has_command': message['seed_is_command']})

    wake = SpeechWakeFallback(vad, stt, lambda: True, detected,
                              speech_probability=probability, verify=verify)
    started = time.monotonic()
    try:
        sample = pcm + bytes(32000)
        for offset in range(0, len(sample), 3200):
            await wake.feed(sample[offset:offset + 3200])
        if wake.task:
            await wake.task
        return {'matched': bool(matches), 'matches': matches,
                'elapsed_ms': round((time.monotonic() - started) * 1000),
                'vad': wake.snapshot()}
    finally:
        await wake.close()


async def model_check(config_path):
    local = json.loads(config_path.read_text()).get('providers', {}).get('local_llama', {})
    config = _merge(DEFAULT_CONFIG['providers']['local_llama'], local)
    config.update(host='127.0.0.1', prewarm=True, prompt_cache=False,
                  max_output_tokens=8, threads=1, threads_batch=1)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        config['port'] = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='carlos-offline-model-') as temp:
        model = LocalLlamaProvider(config, ownership_path=Path(temp) / 'owner.json')
        try:
            started = time.monotonic()
            await model.prewarm()
            load_seconds = round(time.monotonic() - started, 3)
            started = time.monotonic()
            result = await model._post([{'role': 'user', 'content': 'Reply with the word hello.'}], [])
            assert result['choices'][0]['message']['content'].strip(), 'Empty local-model reply'
            print(json.dumps({'local_model': 'owned local server replied without an external route',
                              'load_seconds': load_seconds,
                              'reply_seconds': round(time.monotonic() - started, 3)}), flush=True)
        finally:
            await model.close()


async def main(config_path, keywords, samples, include_model):
    parent = os.environ.get('CARLOS_TEST_PARENT_NETNS')
    if not parent or os.readlink('/proc/self/ns/net') == parent:
        raise RuntimeError('Run inside a new network namespace; see --help')
    if Path('/proc/net/route').read_text().strip().splitlines()[1:]:
        raise RuntimeError('The fixture namespace has an external route')
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    with socket.socket() as probe:
        probe.settimeout(.2)
        if probe.connect_ex(('192.0.2.1', 443)) == 0:
            raise RuntimeError('External test address was reachable')
    local = json.loads(config_path.read_text())
    voice = _merge(DEFAULT_CONFIG['voice'], local.get('voice', {}))
    if keywords:
        voice['wake']['keywords'] = str(keywords.resolve())
    del local
    if voice['tts']['provider'] != 'piper' or not voice['vad']['neural_enabled']:
        raise RuntimeError('This acceptance check requires Piper and neural VAD')
    tts = TtsRouter(voice['tts'])
    vad = NeuralVadWorker(voice['vad'])
    with tempfile.TemporaryDirectory(prefix='carlos-offline-voice-') as temp:
        stt = WhisperCppAdapter(voice['stt'], Path(temp))
        preview_config = {**voice['stt'],
                          'server_port': int(voice['stt'].get('server_port', 18082)) + 1,
                          **voice.get('preview_stt', {})}
        preview = WhisperCppAdapter(preview_config, Path(temp) / 'preview')
        try:
            await stt.prewarm()
            await preview.prewarm()
            failed = []
            for index, (phrase, expected) in enumerate((
                ('Hey Carlos, what is my CPU usage?', True),
                ('Carlos.', True),
                ('Carlos.', True),
                ('Carlos.', True),
                ('Every evening we leave the television on.', False),
                ('Believe me, this movie is amazing.', False),
                ('Car loans are getting expensive.', False),
                ('Call us when you get home.', False),
            )):
                sample_path = samples / f'{index}.pcm' if samples else None
                if sample_path and sample_path.exists():
                    pcm = sample_path.read_bytes()
                    synthesis = {'tts_engine': 'cached Piper sample'}
                else:
                    audio = await tts.synthesize(phrase)
                    pcm = await resample(audio)
                    synthesis = {'tts_engine': audio.engine, 'tts_ms': round(audio.latency_ms)}
                    if sample_path:
                        samples.mkdir(parents=True, exist_ok=True)
                        sample_path.write_bytes(pcm)
                transcript = await stt.transcribe(pcm)
                keyword = await keyword_check(voice['wake'], pcm)
                backup = await speech_check(vad, preview, pcm,
                                           voice['vad'].get('speech_probability', .45),
                                           stt.transcribe)
                report = {'phrase': phrase, 'expected_wake': expected,
                          **synthesis,
                          'stt_text': transcript.raw, 'keyword_worker': keyword,
                          'speech_backup': backup, 'network': 'isolated; loopback only'}
                print(json.dumps(report), flush=True)
                if keyword['matched'] != expected or backup['matched'] != expected:
                    failed.append(index)
            if failed:
                raise RuntimeError(f'Wake mismatches at sample indexes: {failed}')
        finally:
            await tts.close()
            await vad.close()
            await stt.close()
            await preview.close()
    if include_model:
        await model_check(config_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Requires installed local voice models. Example: '
        'CARLOS_TEST_PARENT_NETNS=$(readlink /proc/self/ns/net) '
        'unshare --user --map-root-user --net python3 scripts/check-offline-voice.py --run. '
        'No microphone, playback, cloud request or desktop action is used.'))
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', type=Path,
                        default=Path.home() / '.config/ev/config.json')
    parser.add_argument('--keywords', type=Path, help='Alternate keyword file to validate')
    parser.add_argument('--samples', type=Path,
                        help='Cache synthetic PCM for comparisons with identical audio')
    parser.add_argument('--include-model', action='store_true',
                        help='Also load an owned local conversation model and request a short reply')
    args = parser.parse_args()
    asyncio.run(main(args.config, args.keywords, args.samples, args.include_model))
