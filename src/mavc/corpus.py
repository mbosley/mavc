"""Bounded cases through a flow's existing launcher; logs stream to disk."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time


def _stop(process):
    """Terminate only the owned group, including descendants of an exited child."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def run_cases(cases, folder, command, *, workers=1, timeout=3600):
    """command(case, fresh_run_folder) returns argv, never shell text.

    Keep the manifest/declarations as the record of per-case model settings.
    Status means child execution only; domain acceptance belongs to the flow.
    A corpus destination must be fresh, including after a failed execution.
    """
    cases = list(cases)
    identities = [case.get('id') for case in cases]
    if not cases or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', i)
                        for i in identities):
        raise ValueError('case IDs must be nonempty safe folder names')
    if len(set(identities)) != len(identities):
        raise ValueError('duplicate case IDs')
    if workers < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('positive workers and finite positive case timeout required')
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'cases.json').write_text(json.dumps(cases, indent=2) + '\n')
    stop = threading.Event()

    def execute(case):
        case_folder = folder / case['id']
        case_folder.mkdir()
        result = {'id': case['id'], 'status': 'failed', 'returncode': None,
                  'run_dir': str(case_folder / 'run'), 'started_unix': time.time()}
        process = None
        try:
            argv = command(case, case_folder / 'run')
            result['command'] = argv
            with (case_folder / 'stdout.log').open('wb') as stdout, (case_folder / 'stderr.log').open('wb') as stderr:
                if stop.is_set():
                    result['status'] = 'interrupted'
                    return result
                process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True)
                result['pid'] = result['process_group'] = process.pid
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if stop.is_set() or time.monotonic() >= deadline:
                        result['status'] = 'interrupted' if stop.is_set() else 'timed_out'
                        break
                    time.sleep(.05)
                else:
                    result['status'] = 'completed' if process.returncode == 0 else 'failed'
        except Exception as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if process is not None:
                _stop(process)
                result['returncode'] = process.returncode
            result['ended_unix'] = time.time()
            (case_folder / 'status.json').write_text(json.dumps(result, indent=2) + '\n')
        return result

    previous_term = None
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.getsignal(signal.SIGTERM)
        def interrupted(signum, frame):
            raise KeyboardInterrupt('corpus interrupted')
        signal.signal(signal.SIGTERM, interrupted)
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = []
    interrupted_run = False
    try:
        futures = [pool.submit(execute, case) for case in cases]
        for future in futures:
            future.result()
    except BaseException:
        interrupted_run = True
        stop.set()
        raise
    finally:
        pool.shutdown(wait=True)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        results = [json.loads((folder / case['id'] / 'status.json').read_text()) for case in cases
                   if (folder / case['id'] / 'status.json').exists()]
        (folder / 'summary.json').write_text(json.dumps({'interrupted': interrupted_run, 'cases': results}, indent=2) + '\n')
    return results


def add_arguments(parser):
    parser.add_argument('--cases', type=Path, help='JSON list of IDs and existing launcher arguments')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--case-timeout', type=float, default=3600)


def launch(args, parser, launcher, allowed):
    """Thin glue shared by the existing corpus launcher modes."""
    if not args.cases or not args.run_dir:
        parser.error('corpus requires --cases and a fresh --run-dir')
    manifest = args.cases.resolve()
    cases = json.loads(manifest.read_text())
    def command(case, folder):
        mode = case.get('mode', 'replay')
        if mode not in ('replay', 'run') or set(case) - {'id', 'mode', *allowed}:
            raise ValueError(f'invalid launcher case {case["id"]!r}')
        argv = [sys.executable, str(Path(launcher).resolve()), mode, '--run-dir', str(folder)]
        for key in allowed:
            if key in case:
                path = Path(case[key])
                argv += ['--' + key.replace('_', '-'), str((manifest.parent / path).resolve())]
        return argv
    results = run_cases(cases, args.run_dir, command, workers=args.workers, timeout=args.case_timeout)
    print(json.dumps(results, indent=2))
    return 0 if all(result['status'] == 'completed' for result in results) else 1
