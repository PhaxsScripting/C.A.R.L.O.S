"""On-demand Core process-tree measurements; separate services stay separate."""

import asyncio
import os
import time

import psutil


MAXIMUM_PROCESSES = 64


def read_tree(root):
    processes = [root, *root.children(recursive=True)]
    limited = len(processes) > MAXIMUM_PROCESSES
    rows, unavailable = {}, 0
    for process in processes[:MAXIMUM_PROCESSES]:
        try:
            with process.oneshot():
                key = process.pid, process.create_time()
                cpu = process.cpu_times()
                memory = process.memory_info()
                try:
                    pss = getattr(process.memory_full_info(), 'pss', None)
                except psutil.AccessDenied:
                    pss = None
                if psutil.Process(process.pid).create_time() != key[1]:
                    unavailable += 1
                    continue
                rows[key] = {'cpu_seconds': cpu.user + cpu.system, 'rss_bytes': memory.rss, 'pss_bytes': pss}
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            unavailable += 1
    return {'rows': rows, 'complete': not limited and unavailable == 0,
            'limit_reached': limited, 'unavailable': unavailable, 'discovered': len(processes)}


async def measure_tree(root, seconds, reader=read_tree, clock=time.monotonic):
    identity = root.pid, root.create_time()
    before = await asyncio.to_thread(reader, root)
    started = clock()
    await asyncio.sleep(seconds)
    after = await asyncio.to_thread(reader, root)
    elapsed = clock() - started
    rows = after['rows']
    if identity not in before['rows'] or identity not in rows:
        return {'ok': False, 'error': 'Core process identity could not be observed throughout the measurement'}
    complete = after['complete']
    pss_complete = complete and all(row['pss_bytes'] is not None for row in rows.values())
    stable = complete and before['complete'] and set(rows) == set(before['rows'])
    deltas = [row['cpu_seconds'] - before['rows'][key]['cpu_seconds']
              for key, row in rows.items() if key in before['rows']]
    cpu_complete = stable and elapsed > 0 and all(delta >= 0 for delta in deltas)
    rss_observed = sum(row['rss_bytes'] for row in rows.values())
    pss_observed = sum(row['pss_bytes'] or 0 for row in rows.values())
    result = {'scope': 'Core OS process tree only; excludes separate UI, pet, mobile services and reparented runtimes',
            'elapsed_seconds': round(elapsed, 3), 'observed_at': time.time(),
            'process_count': len(rows), 'discovered_processes': after['discovered'],
            'process_limit': MAXIMUM_PROCESSES, 'process_limit_reached': after['limit_reached'],
            'unavailable_processes': after['unavailable'],
            'rss_total_bytes': rss_observed if complete else None,
            'rss_observed_bytes': rss_observed,
            'pss_total_bytes': pss_observed if pss_complete else None,
            'pss_observed_bytes': pss_observed,
            'memory_complete': complete, 'pss_complete': pss_complete,
            'cpu_complete': cpu_complete,
            'cpu_percent_one_core': round(sum(deltas) / elapsed * 100, 3) if cpu_complete else None,
            'rss_note': 'Summed RSS repeats shared pages; PSS apportions shared pages where available',
            'cpu_note': 'Interval CPU for continuously observed process identities; includes this measurement overhead. Not a machine idle benchmark'}
    memory_text = (f"The Core process tree uses {pss_observed / 1048576:.0f} MiB proportional memory."
                   if pss_complete else f"The Core process tree sums to {rss_observed / 1048576:.0f} MiB RSS; shared pages may repeat."
                   if complete else "Core process tree memory is incomplete; the observed values are only a lower bound.")
    cpu_text = (f"CPU was {result['cpu_percent_one_core']:.1f}% of one logical CPU over {elapsed:.1f} seconds."
                if cpu_complete else "Interval CPU is unknown because process identities or readings changed.")
    result['message'] = memory_text + ' ' + cpu_text + ' Separate UI, pet, mobile and reparented runtimes are excluded.'
    return result


async def measure_core(arguments, context):
    return await measure_tree(psutil.Process(os.getpid()), arguments.get('seconds', 3))


def register_core_resources(registry):
    from .tools.base import ToolSpec
    from .permissions import Permission

    registry.register(ToolSpec('system.carlos_resources', 'SYSTEM',
        'Read current Core plus OS-descended worker memory and interval CPU. No process control. '
        'Separate UI/pet/mobile and reparented runtimes are excluded. RSS repeats shared pages; PSS may be unavailable.',
        Permission.SAFE, {'type': 'object', 'properties': {'seconds': {'type': 'number', 'minimum': 1, 'maximum': 15}},
                          'additionalProperties': False}, measure_core,
        read_only=True, offline_available=True, timeout_seconds=20,
        verification='Bounded process identities, memory readings and interval counters; not idle or performance acceptance'))
