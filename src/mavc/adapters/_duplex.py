"""Tool frames on the same owned Node process; Python dispatch remains synchronous."""
import json
import os
import selectors
import threading
import time


def exchange(child, request, dispatch, out, deadline, cap, stop):
    timer = None if deadline is None else threading.Timer(max(0, deadline - time.monotonic()), stop, (child, False))
    if timer:
        timer.start()
    def check_live():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Pi call timed out')
    def send(frame):
        encoded = (json.dumps(frame, ensure_ascii=False, allow_nan=False) + '\n').encode()
        if cap is not None and len(encoded) > cap:
            raise ValueError('Pi tool frame exceeded declared max_output_bytes')
        with selectors.DefaultSelector() as writable:
            writable.register(child.stdin, selectors.EVENT_WRITE)
            while encoded:
                check_live()
                if writable.select(.05):
                    encoded = encoded[os.write(child.stdin.fileno(), encoded):]
    os.set_blocking(child.stdin.fileno(), False)
    pending = b''
    response, completed = None, False
    try:
        send({'type':'mavc_call', 'request':request})
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                check_live()
                if not selector.select(.05):
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    break
                out.write(chunk)
                if cap is not None and out.tell() > cap:
                    raise ValueError('Pi stdout exceeded declared max_output_bytes')
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    frame = json.loads(line)
                    if frame['type'] == 'mavc_response' and response is None:
                        response = frame['response']
                    elif frame['type'] in ('mavc_tool_call', 'mavc_session_stopped') and response is None:
                        if dispatch is None:
                            raise ValueError('Pi requested a Python tool without a bound dispatcher')
                        reply = dispatch({**frame, 'deadline_monotonic':deadline,
                            'cancelled':lambda: not completed and child.poll() is not None})
                        check_live()
                        send({'type':'mavc_tool_result', 'id':frame['id'], 'result':reply})
                    else:
                        raise ValueError('Invalid Pi tool frame sequence')
        child.wait()
        if pending or not isinstance(response, dict):
            raise ValueError('Incomplete Pi tool response')
        check_live()
        completed = child.returncode == 0
        return json.dumps(response, ensure_ascii=False, allow_nan=False), None
    except Exception as error:
        stop(child, False)
        expired = deadline is not None and time.monotonic() >= deadline
        return None, 'Pi call timed out' if expired else str(error)
    finally:
        if timer:
            timer.cancel()
            timer.join()
