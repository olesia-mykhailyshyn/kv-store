"""Helpers for integration tests: real coordinator/shard processes on localhost.

Each process is started exactly like on a server (python -m kvstore.<module> ...) on a free
port, and a test waits for GET /health before it sends requests. Process output goes to a
log file in the test's tmp dir, so a failing test can be debugged from that file.

Responses are checked against the schemas in api/openapi.yaml (the frozen contract).
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
OPENAPI = os.path.join(ROOT, "api", "openapi.yaml")


def free_port() -> int:
    """Ask the OS for an unused port (bind to port 0), then release it for the process."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base_url: str, method: str, path: str, body=None,
         raw: Optional[bytes] = None) -> Tuple[int, Optional[dict], bytes]:
    """One HTTP request on a new connection -> (status, parsed JSON or None, raw body)."""
    host_port = base_url.split("://", 1)[1]
    conn = http.client.HTTPConnection(host_port, timeout=10)
    try:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Content-Type": "application/json"} if data is not None else {}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        content = resp.read()
    finally:
        conn.close()
    parsed = json.loads(content) if content else None
    return resp.status, parsed, content


class Process:
    """A kvstore service running as a child process."""

    def __init__(self, args: List[str], port: int, log_path: str):
        self.args = args
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.log_path = log_path
        self.popen: Optional[subprocess.Popen] = None

    def start(self) -> "Process":
        env = dict(os.environ, PYTHONPATH=SRC, PYTHONUNBUFFERED="1")
        log = open(self.log_path, "a")
        self.popen = subprocess.Popen([sys.executable, "-m"] + self.args,
                                      stdout=log, stderr=subprocess.STDOUT, env=env)
        log.close()  # the child has its own copy of the file descriptor
        self.wait_healthy()
        return self

    def wait_healthy(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.popen.poll() is not None:
                raise RuntimeError(f"process exited, see {self.log_path}:\n{self.log()}")
            try:
                status, _, _ = call(self.url, "GET", "/health")
                if status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.05)
        raise RuntimeError(f"no /health within {timeout}s, see {self.log_path}")

    def kill(self) -> None:
        """kill -9: no clean shutdown, like a crashed server."""
        if self.popen and self.popen.poll() is None:
            self.popen.kill()
            self.popen.wait()

    def log(self) -> str:
        with open(self.log_path) as f:
            return f.read()


class Launcher:
    """Starts coordinator and shard processes with files under base_dir; kill_all() at the end."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.started: List[Process] = []

    def coordinator(self, mode: str, port: Optional[int] = None) -> Process:
        """Same port and data dir on every call, so calling it again = restart."""
        port = port or free_port()
        proc = Process(["kvstore.coordinator", "--mode", mode, "--host", "127.0.0.1",
                        "--port", str(port), "--data-dir", self._path("coordinator")],
                       port, self._path("coordinator.log"))
        self.started.append(proc)
        return proc.start()

    def shard(self, shard_id: str, coordinator_url: str, port: Optional[int] = None) -> Process:
        """Heartbeat every 1 s, so tests wait for the ring at most a couple of seconds."""
        port = port or free_port()
        proc = Process(["kvstore.shard", "--id", shard_id, "--host", "127.0.0.1",
                        "--port", str(port), "--data-dir", self._path(shard_id),
                        "--coordinator-url", coordinator_url,
                        "--advertise-url", f"http://127.0.0.1:{port}",
                        "--heartbeat-seconds", "1"],
                       port, self._path(f"{shard_id}.log"))
        self.started.append(proc)
        return proc.start()

    def kill_all(self) -> None:
        for proc in self.started:
            proc.kill()

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, name)


@pytest.fixture
def launcher(tmp_path):
    """A Launcher for one test; every process it started is killed afterwards."""
    launch = Launcher(str(tmp_path))
    yield launch
    launch.kill_all()


def wait_for(check, timeout: float = 10.0, interval: float = 0.1) -> float:
    """Poll check() until it returns True; returns the seconds it took, fails after timeout."""
    began = time.time()
    while time.time() - began < timeout:
        if check():
            return time.time() - began
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


# --- OpenAPI schema checks -------------------------------------------------------------

def _load_openapi() -> dict:
    with open(OPENAPI) as f:
        return yaml.safe_load(f)


_SPEC = _load_openapi()
_validators: Dict[str, Draft202012Validator] = {}


def assert_schema(schema_name: str, instance) -> None:
    """Validate instance against components/schemas/<schema_name> of api/openapi.yaml.

    OpenAPI 3.1 schemas are JSON Schema 2020-12. The wrapper schema carries the whole
    "components" section, so every "#/components/schemas/..." $ref resolves inside it.
    """
    if schema_name not in _validators:
        wrapper = {"$ref": f"#/components/schemas/{schema_name}",
                   "components": _SPEC["components"]}
        _validators[schema_name] = Draft202012Validator(wrapper)
    _validators[schema_name].validate(instance)


def assert_error(status_and_body, status: int, code: str) -> None:
    """A 4xx/5xx answer with the Error body {"error","message"} and the given code."""
    actual_status, body, _ = status_and_body
    assert actual_status == status, body
    assert_schema("Error", body)
    assert body["error"] == code
