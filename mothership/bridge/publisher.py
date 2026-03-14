"""
ZeroMQ bridge — publishes fleet state as a 1Hz JSON stream.
Requires: pip install pyzmq
Falls back gracefully if pyzmq is not installed.
"""
import json
import threading
import time
from memory import store

PUBLISH_PORT = 5555
COMMAND_PORT = 5556

_pub_socket = None
_sub_socket = None
_running    = False
_callbacks  = []


def start():
    global _pub_socket, _sub_socket, _running
    try:
        import zmq
    except ImportError:
        print("[bridge/zmq] pyzmq not installed — falling back to file bridge")
        return False

    ctx = zmq.Context()

    _pub_socket = ctx.socket(zmq.PUB)
    _pub_socket.bind(f"tcp://*:{PUBLISH_PORT}")

    _sub_socket = ctx.socket(zmq.SUB)
    _sub_socket.bind(f"tcp://*:{COMMAND_PORT}")
    _sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    _running = True
    threading.Thread(target=_publish_loop, daemon=True).start()
    threading.Thread(target=_receive_loop, daemon=True).start()
    return True


def _publish_loop():
    while _running:
        state = {
            "ru_balance":    store.get("ru_balance", 0),
            "ships":         store.get("active_agents", []),
            "objective":     store.get("current_objective", ""),
            "findings":      store.get("findings_count", 0),
            "harvest_count": len(store.get("harvest_refs", [])),
        }
        try:
            _pub_socket.send_string(json.dumps(state))
        except Exception:
            pass
        time.sleep(1.0)


def _receive_loop():
    while _running:
        try:
            import zmq
            msg = _sub_socket.recv_string(flags=zmq.NOBLOCK)
            event = json.loads(msg)
            for cb in _callbacks:
                cb(event)
        except Exception:
            time.sleep(0.1)


def on_event(callback):
    _callbacks.append(callback)


def send_command(cmd: dict):
    if _pub_socket:
        try:
            _pub_socket.send_string(json.dumps({"__cmd__": True, **cmd}))
        except Exception:
            pass


def stop():
    global _running
    _running = False
