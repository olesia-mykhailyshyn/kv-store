"""kill -9 right after a write was confirmed (spec shard-storage, "Падіння після підтвердження").

A real child process writes through Storage and only then prints "ok" (in the shard,
"ok" is the 201 response). We SIGKILL it: no close(), no flush at exit, no cleanup.
If put() returned only after fsync, the item must be read back after "restart".
"""
import os
import signal
import subprocess
import sys

from kvstore.storage import Storage

CHILD = """
import sys, time
from kvstore.storage import Storage
s = Storage(sys.argv[1])
s.put("orders", "42", "2026-11-05", {"total": 300})
print("ok", flush=True)
time.sleep(60)
"""


def test_item_survives_kill_9_after_confirmed_put(tmp_path):
    wal = str(tmp_path / "wal.log")
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    child = subprocess.Popen([sys.executable, "-c", CHILD, wal],
                             env=dict(os.environ, PYTHONPATH=src),
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ok"
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
        child.stdout.close()

    assert child.returncode == -signal.SIGKILL
    s = Storage(wal)
    assert s.get("orders", "42", "2026-11-05") == {"total": 300}
    s.close()
