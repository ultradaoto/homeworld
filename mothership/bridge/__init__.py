from bridge.state_file import start as _file_start, stop as _file_stop
from bridge.state_file import STATE_FILE, write_command
import os


def start(mode: str = "file"):
    """mode: 'file' (no deps, default) or 'zmq' (full duplex bridge)"""
    if mode == "zmq":
        from bridge.publisher import start as zmq_start
        if not zmq_start():
            # ZMQ unavailable — fall through to file
            _file_start()
    else:
        _file_start()


def stop():
    try:
        from bridge.publisher import stop as zmq_stop
        zmq_stop()
    except Exception:
        pass
    try:
        _file_stop()
    except Exception:
        pass
