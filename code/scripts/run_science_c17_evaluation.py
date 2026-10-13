#!/usr/bin/env python3
"""Run offline C17 forward evaluation; prohibit all Python socket network calls."""
import argparse
import json
import socket
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'veritmm')]

def offline_audit(event, args):
    if event.startswith('socket.'):
        raise RuntimeError('C17 offline evaluation forbids network access')

sys.addaudithook(offline_audit)
from optomind_eval.task_runner import run_evaluation

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--tasks-per-case', type=int, default=16)
    args = parser.parse_args()
    report = run_evaluation(args.output_dir, tasks_per_case=args.tasks_per_case)
    print(json.dumps(report, indent=2))
