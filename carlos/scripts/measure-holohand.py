#!/usr/bin/env python3
"""Read the existing hand controller's metadata without launching or changing it."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from ev.hand_metrics import sample
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--seconds',type=float,default=5)
parser.add_argument('--output',type=Path)
args=parser.parse_args()
report=json.dumps(asyncio.run(sample(args.seconds)),indent=2)+'\n'
if args.output: args.output.write_text(report)
print(report,end='')
