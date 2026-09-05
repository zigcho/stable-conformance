"""Local metadata fixture, not a mock of either server under comparison."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from benchmarks.fixtures import beatmap


def start():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/search":
                self.send_error(404)
                return
            query = parse_qs(parsed.query).get("query", [""])[0].casefold()
            sets = []
            for index in range(20):
                title = f"isolated workload {index}"
                if query and query not in title:
                    continue
                sets.append({"Artist": "zigcho benchmark", "Title": title, "Creator": "bench_0",
                             "RankedStatus": 1, "LastUpdate": "2026-09-05 00:00:00", "SetID": beatmap(index).id,
                             "HasVideo": False, "ChildrenBeatmaps": [{"DifficultyRating": 0, "DiffName": "synthetic",
                             "CS": 4, "OD": 6, "AR": 8, "HP": 5, "Mode": 0}]})
            body = json.dumps(sets).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 18999), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
