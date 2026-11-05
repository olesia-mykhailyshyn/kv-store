"""HTTP plumbing shared by the coordinator and the shard (ADR-005, design decision 5).

Standard library only: http.server.ThreadingHTTPServer, one thread per connection.
protocol_version "HTTP/1.1" keeps connections open between requests (keep-alive), so a
client like YCSB does not pay for a new TCP handshake on every operation.

What lives here, so that coordinator.py and shard.py contain only their own logic:
- a tiny router: "/tables/{table}/items/{pk}" -> handler(request) with params;
- path segments are URL-decoded one by one AFTER splitting on "/", so a key with an
  encoded slash ("a%2Fb") stays one segment;
- the request body is read up front and limited to 64 KiB (-> 413);
- every error is JSON {"error": "<code>", "message": "..."} (openapi Error), except HEAD,
  which never has a body.
"""
import json
import logging
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

log = logging.getLogger("kvstore.http")

MAX_BODY_BYTES = 64 * 1024

# HTTP status -> "error" field of the Error body (enum in api/openapi.yaml)
ERROR_CODES = {
    400: "bad_request",
    404: "not_found",
    405: "bad_request",
    409: "conflict",
    413: "payload_too_large",
    500: "internal",
    501: "bad_request",
    503: "shard_unavailable",
}


class HttpError(Exception):
    """Raised by a handler to answer with an Error body: raise HttpError(404, "no such table")."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    """What a handler gets: method, path params, query params, the raw body, the sender's IP."""

    def __init__(self, method: str, params: Dict[str, str], query: Dict[str, str], body: bytes,
                 client_ip: str = ""):
        self.method = method
        self.params = params
        self.query = query
        self.body = body
        self.client_ip = client_ip  # IP address the TCP connection comes from

    def json(self) -> dict:
        """The body as a JSON object; anything else is a 400."""
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HttpError(400, "body is not valid JSON")
        if not isinstance(data, dict):
            raise HttpError(400, "body must be a JSON object")
        return data


# A handler returns (status, JSON-able body or None for an empty body)
Response = Tuple[int, Optional[object]]
Handler = Callable[[Request], Response]


class Router:
    """Maps (method, path pattern) to a handler. Patterns: "/tables/{table}/items"."""

    def __init__(self):
        self._routes: List[Tuple[str, List[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append((method, pattern.strip("/").split("/"), handler))

    def match(self, method: str, segments: List[str]) -> Tuple[Optional[Handler], Dict[str, str]]:
        """(handler, params). (None, {}) if the path is unknown -> 404;
        raises 405 if the path exists but not for this method."""
        path_known = False
        for route_method, pattern, handler in self._routes:
            params = _match_segments(pattern, segments)
            if params is None:
                continue
            if route_method == method:
                return handler, params
            path_known = True
        if path_known:
            raise HttpError(405, f"method {method} is not allowed here")
        return None, {}


def _match_segments(pattern: List[str], segments: List[str]) -> Optional[Dict[str, str]]:
    if len(pattern) != len(segments):
        return None
    params = {}
    for expected, actual in zip(pattern, segments):
        if expected.startswith("{"):
            if not actual:
                return None  # empty segment ("items//x") is not a value
            params[expected[1:-1]] = actual
        elif expected != actual:
            return None
    return params


def split_path(raw_path: str) -> Tuple[List[str], Dict[str, str]]:
    """'/tables/t/items/a%2Fb?skFrom=1' -> (['tables','t','items','a/b'], {'skFrom': '1'})."""
    parts = urlsplit(raw_path)
    try:
        # strict: "%FF" and "%FE" must not both turn into the same replacement character
        segments = [unquote(s, errors="strict") for s in parts.path.strip("/").split("/")]
    except UnicodeDecodeError:
        raise HttpError(400, "path is not valid UTF-8")
    # a repeated query parameter keeps its first value
    query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    return segments, query


class JsonHandler(BaseHTTPRequestHandler):
    """Base request handler; make_server() gives each server a subclass with its router."""

    protocol_version = "HTTP/1.1"
    # TCP_NODELAY: the headers and the body go out as two writes. With Nagle's algorithm
    # the body waits for the ACK of the headers, and the client delays that ACK (~40 ms),
    # so every request on a kept-alive connection would take ~40 ms.
    disable_nagle_algorithm = True
    # An idle keep-alive connection is closed after 60 s, so it does not hold a thread forever.
    # The coordinator retries once on a fresh connection when a shard closed an idle one.
    timeout = 60
    router: Router

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            body = self._read_body()
            segments, query = split_path(self.path)
            handler, params = self.router.match(method, segments)
            if handler is None:
                raise HttpError(404, f"no route {self.path}")
            status, payload = handler(Request(method, params, query, body,
                                              self.client_address[0]))
        except HttpError as e:
            status, payload = e.status, {"error": ERROR_CODES.get(e.status, "internal"),
                                         "message": e.message}
        except Exception:  # a bug must not kill the connection thread silently
            log.exception("%s %s failed", method, self.path)
            status, payload = 500, {"error": "internal", "message": "internal error"}
        self._send(method, status, payload)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            # the chunked body is not read, it would be parsed as the next request: close
            self.close_connection = True
            raise HttpError(400, "chunked bodies are not supported, send Content-Length")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            # rfile.read(-1) would wait until the client closes the connection
            self.close_connection = True
            raise HttpError(400, "bad Content-Length")
        if length > MAX_BODY_BYTES:
            # the unread body would be parsed as the next request: close the connection
            self.close_connection = True
            raise HttpError(413, f"body is larger than {MAX_BODY_BYTES} bytes")
        return self.rfile.read(length) if length else b""

    def send_error(self, code, message=None, explain=None):
        """Errors that http.server answers itself (unknown method such as PATCH, broken or
        too long request line) also get the JSON Error body, not the default HTML page."""
        self.close_connection = True
        self._send(getattr(self, "command", None) or "GET", code,
                   {"error": ERROR_CODES.get(code, "bad_request"), "message": message or ""})

    def _send(self, method: str, status: int, payload: Optional[object]) -> None:
        data = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if method != "HEAD" and status != 204:
            self.wfile.write(data)

    def log_message(self, format, *args):
        # one line per request would flood the log under YCSB: only at DEBUG level
        log.debug("%s %s", self.address_string(), format % args)


class Server(ThreadingHTTPServer):
    daemon_threads = True      # do not wait for open keep-alive connections on exit
    request_queue_size = 128   # listen backlog for many client threads (default 5); read at bind


def make_server(router: Router, host: str, port: int) -> Server:
    """Bind the port now (so it is ready before anyone is told about it); the caller then
    runs server.serve_forever()."""

    class Handler(JsonHandler):
        pass

    Handler.router = router
    server = Server((host, port), Handler)
    log.info("listening on %s:%d", host, port)
    return server


def setup_logging() -> None:
    """Log to stdout: systemd puts it into the journal (journalctl -u kvstore-...)."""
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_KEY_LENGTH = 1024


def is_name(value) -> bool:
    """Table names, key attribute names and shard ids: [A-Za-z0-9_-]{1,64} (openapi)."""
    # fullmatch: the whole string must match ("$" would also accept a trailing \n)
    return isinstance(value, str) and NAME_PATTERN.fullmatch(value) is not None


def is_key_value(value) -> bool:
    """PK and SK values: strings of 1..1024 characters (openapi KeyValue)."""
    return isinstance(value, str) and 1 <= len(value) <= MAX_KEY_LENGTH
