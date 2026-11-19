"""Bounded read-only checks with ordered results and stderr progress."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import multiprocessing
import sys
import time


def positive_workers(value:str)->int:
    try:workers=int(value)
    except ValueError:raise argparse.ArgumentTypeError('workers must be an integer >= 1')
    if workers<1:raise argparse.ArgumentTypeError('workers must be >= 1')
    return workers


def add_workers_argument(parser)->None:
    parser.add_argument('--workers',type=positive_workers,default=4,
                        help='Workers for read-only checks (default: 4; 1 runs serially)')


@contextmanager
def timed_phase(label:str):
    started=time.monotonic()
    print(f'[DLB] {label}: start',file=sys.stderr,flush=True)
    try:yield
    except BaseException:
        print(f'[DLB] {label}: failed ({time.monotonic()-started:.2f}s)',file=sys.stderr,flush=True)
        raise
    else:
        print(f'[DLB] {label}: done ({time.monotonic()-started:.2f}s)',file=sys.stderr,flush=True)


def ordered_checks(function,tasks,workers:int,label:str,*,processes:bool=False)->list:
    tasks=list(tasks)
    count=min(workers,len(tasks))
    with timed_phase(f'{label} [{len(tasks)} tasks, {count} workers]'):
        if not tasks:return []
        results=[None]*len(tasks)
        last_progress=time.monotonic()
        def record(index,result,completed):
            nonlocal last_progress
            results[index]=result
            now=time.monotonic()
            if now-last_progress>=2:
                print(f'[DLB] {label}: {completed}/{len(tasks)}',file=sys.stderr,flush=True)
                last_progress=now
        if count==1:
            for index,task in enumerate(tasks):record(index,function(task),index+1)
        else:
            pool_type=ProcessPoolExecutor if processes else ThreadPoolExecutor
            kwargs={'mp_context':multiprocessing.get_context('spawn')} if processes else {}
            with pool_type(max_workers=count,**kwargs) as pool:
                pending={pool.submit(function,task):index for index,task in enumerate(tasks)}
                try:
                    for completed,future in enumerate(as_completed(pending),1):
                        record(pending[future],future.result(),completed)
                except BaseException:
                    for future in pending:future.cancel()
                    raise
        return results
