import threading

_locks: dict = {}   # filename → agent_id
_mutex = threading.Lock()


def acquire(filename: str, agent_id: str) -> bool:
    """Returns True if agent got the lock, False if file is contested."""
    with _mutex:
        if filename in _locks and _locks[filename] != agent_id:
            return False
        _locks[filename] = agent_id
        return True


def release(filename: str, agent_id: str):
    with _mutex:
        if _locks.get(filename) == agent_id:
            del _locks[filename]


def contested_files() -> list:
    with _mutex:
        return list(_locks.keys())
