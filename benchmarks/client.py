"""Async HTTP/1.1 client with a fixed concurrency and body ceiling, loopback only."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Response:
    status: int = 0
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0
    error: str | None = None


class Client:
    def __init__(self, origin: str, concurrency: int = 2048, timeout: float = 30):
        url = urlsplit(origin)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path not in ("", "/") or url.query or url.fragment or url.username:
            raise ValueError("benchmark HTTP origin must be explicit loopback http://127.0.0.1:port")
        if not 1 <= concurrency <= 4096 or not 0 < timeout <= 120:
            raise ValueError("invalid bounded client limits")
        self.port = url.port
        self.limit = concurrency
        self.active = 0
        self.peak = 0
        self.timeout = timeout
        self.max_body = 2 * 1024 * 1024

    async def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None, *, player: int = 0) -> Response:
        if self.active >= self.limit:
            return Response(error="generator_capacity")
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"} or not path.startswith("/") or "\r" in path or "\n" in path or len(body) > self.max_body:
            raise ValueError("invalid bounded request")
        self.active += 1
        self.peak = max(self.peak, self.active)
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout):
                response = await self._request(method, path, body, headers or {}, player)
            response.elapsed_ms = (time.perf_counter() - started) * 1000
            return response
        except TimeoutError:
            return Response(elapsed_ms=(time.perf_counter() - started) * 1000, error="timeout")
        except (OSError, EOFError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            return Response(elapsed_ms=(time.perf_counter() - started) * 1000, error="transport_or_response")
        finally:
            self.active -= 1

    async def _request(self, method: str, path: str, body: bytes, headers: dict[str, str], player: int) -> Response:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, limit=65536)
        try:
            fields = {
                "Host": "osu.kai.ovh", "User-Agent": "osu!", "Connection": "close",
                "Content-Length": str(len(body)),
                "CF-Connecting-IP": f"198.18.{player // 254}.{player % 254 + 1}",
                **headers,
            }
            if any("\r" in value or "\n" in value for value in fields.values()):
                raise ValueError("invalid header")
            head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in fields.items()) + "\r\n"
            writer.write(head.encode("ascii") + body)
            await writer.drain()
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode("latin1").split("\r\n")
            status = int(lines[0].split(" ")[1])
            if not 100 <= status <= 599:
                raise ValueError("invalid HTTP status")
            result_headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            result_headers = {key.lower(): value.strip() for key, value in result_headers.items()}
            if "chunked" in result_headers.get("transfer-encoding", "").lower():
                chunks: list[bytes] = []
                size = 0
                while True:
                    line = await reader.readuntil(b"\r\n")
                    amount = int(line.split(b";", 1)[0].strip(), 16)
                    if amount < 0 or size + amount > self.max_body:
                        raise ValueError("response too large")
                    if amount == 0:
                        break
                    chunks.append(await reader.readexactly(amount))
                    if await reader.readexactly(2) != b"\r\n":
                        raise ValueError("invalid chunk")
                    size += amount
                payload = b"".join(chunks)
            elif "content-length" in result_headers:
                amount = int(result_headers["content-length"])
                if not 0 <= amount <= self.max_body:
                    raise ValueError("response too large")
                payload = await reader.readexactly(amount)
            else:
                chunks = []
                size = 0
                while chunk := await reader.read(min(65536, self.max_body + 1 - size)):
                    size += len(chunk)
                    if size > self.max_body:
                        raise ValueError("response too large")
                    chunks.append(chunk)
                payload = b"".join(chunks)
            return Response(status=status, body=payload, headers=result_headers)
        finally:
            writer.close()
