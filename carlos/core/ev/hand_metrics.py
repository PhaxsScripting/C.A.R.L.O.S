"""Rates from owned IPC counters; sampled timing readings are not physical gesture latency."""

import asyncio
import math
import statistics
import time


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    return {'samples':len(values), 'mean':round(statistics.mean(values),2),
            'p50':round(statistics.median(values),2),
            'p95':round(ordered[max(0,math.ceil(len(ordered)*.95)-1)],2),
            'max':round(ordered[-1],2)}


def summarize(samples):
    good = [row for row in samples if row['status'].get('available')]
    report = {'observations':len(samples), 'available_observations':len(good),
              'failed_observations':len(samples)-len(good),
              'capture_fps':None, 'inference_fps':None, 'counter_deltas':None,
              'peer_continuity_verified':False, 'camera_started':False,
              'frames_saved':0, 'gesture_accuracy_verified':False,
              'physical_gesture_latency_ms':None, 'desktop_action_latency_ms':None,
              'scope':'Metadata from an already-running instance; sampled timings may repeat or miss inference results.'}
    for key in ('inference_ms','age_ms'):
        values=[row['status'].get('tracking',{}).get(key) for row in good
                if row['status'].get('pipeline_demand') != 'IDLE']
        values=[v for v in values if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0]
        report['sampled_'+key]=distribution(values)
    report['states']=sorted({row['status'].get('state','UNVERIFIED') for row in good})
    report['pipeline_demands']=sorted({row['status']['pipeline_demand'] for row in good
                                       if row['status'].get('pipeline_demand') in {'ACTIVE','IDLE'}})
    report['hand_visible_observations']=sum(row['status'].get('tracking',{}).get('hand_visible') is True for row in good)
    if len(good)<2:
        report['rate_unavailable_reason']='Not enough available observations'
        return report
    pids={(row['status'].get('peer_pid'),row['status'].get('peer_start_ticks')) for row in good}
    if len(pids)!=1 or any(value is None for value in next(iter(pids))):
        report['rate_unavailable_reason']='Instance identity changed or cannot be verified'
        return report
    counters=[row['status'].get('counters',{}) for row in good]
    keys=('captured','inferred','skipped')
    if any(not all(isinstance(row.get(key),int) and not isinstance(row.get(key),bool) and 0<=row[key]<=2**63-1 for key in keys) for row in counters):
        report['rate_unavailable_reason']='This instance does not expose valid pipeline counters'
        return report
    if any(b[key]<a[key] for a,b in zip(counters,counters[1:]) for key in keys):
        report['rate_unavailable_reason']='Counters reset during observation'
        return report
    elapsed=good[-1]['time']-good[0]['time']
    if not math.isfinite(elapsed) or elapsed<=0:
        report['rate_unavailable_reason']='Observation clock did not advance'
        return report
    report['peer_continuity_verified']=True
    report['elapsed_seconds']=round(elapsed,3)
    report['counter_deltas']={key:counters[-1][key]-counters[0][key] for key in keys}
    report['capture_fps']=round(report['counter_deltas']['captured']/elapsed,2)
    report['inference_fps']=round(report['counter_deltas']['inferred']/elapsed,2)
    return report


async def sample(seconds=5):
    from .tools.holohand import status
    if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 1<=seconds<=30:
        raise ValueError('Observation duration must be 1 to 30 seconds')
    rows=[]
    deadline=time.monotonic()+seconds
    while True:
        before=time.monotonic()
        data=await status({},None)
        after=time.monotonic()
        rows.append({'time':(before+after)/2,'status':data})
        remaining=deadline-after
        if remaining<=0 or not data.get('available'):
            return summarize(rows)
        await asyncio.sleep(min(.2,remaining))
