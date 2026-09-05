"""Loopback-only Host routing for the unmodified reference's domain router.

This does not decode, filter or rewrite application response bodies. The
transcript client deliberately cannot override Host; routing belongs here.
"""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


def start(listen_port: int, upstream_port: int, domain: str):
    if (listen_port, upstream_port, domain) not in {
        (18090, 18190, "kai.ovh"), (18091, 18191, "fixture.invalid")
    }:
        raise ValueError("only the two isolated conformance origins are allowed")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Requests may contain password hashes. Never log them.

        def forward(self):
            if not self.path.startswith("/") or self.path.startswith("//"):
                self.send_error(400)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16 * 1024 * 1024 or "Transfer-Encoding" in self.headers:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in {"host", "connection", "content-length"}}
            headers.update({"Host": ("c." if self.path == "/" else "osu.") + domain,
                            "Connection": "close", "CF-IPCountry": "AU",
                            "CF-IPLatitude": "0", "CF-IPLongitude": "0"})
            connection = HTTPConnection("127.0.0.1", upstream_port, timeout=15)
            try:
                connection.request(self.command, self.path, body, headers)
                response = connection.getresponse()
                data = response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError("response exceeded fixture ceiling")
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, ValueError):
                self.send_error(502, "isolated upstream failed")
            finally:
                connection.close()

        do_GET = forward
        do_POST = forward

    server = ThreadingHTTPServer(("127.0.0.1", listen_port), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    return server
