"""A local stand-in for the Anthropic Messages API: the shape of the documented endpoint, nothing else."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.requests: list[dict] = []
        self.thread = threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def record(self, handler: BaseHTTPRequestHandler, body: bytes = b"") -> None:
        headers = {k.lower(): v for k, v in handler.headers.items()}
        self.requests.append({"path": handler.path, "headers": headers, "body": body})


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


class FakeAnthropic(_Server):
    def __init__(self, reply: str = "", status: int = 200) -> None:
        self.reply = reply
        self.status = status

        class H(_Quiet):
            def do_POST(self) -> None:  # noqa: N802
                owner = self.server.owner  # type: ignore[attr-defined]
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.record(self, body)
                if owner.status != 200:
                    data = json.dumps(
                        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
                    ).encode()
                    self.send_response(owner.status)
                else:
                    data = json.dumps(
                        {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "model": "fake",
                            "content": [{"type": "text", "text": owner.reply}],
                            "stop_reason": "end_turn",
                        }
                    ).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        super().__init__(H)

    def last_request_json(self) -> dict:
        return json.loads(self.requests[-1]["body"])
