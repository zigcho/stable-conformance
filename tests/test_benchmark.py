import asyncio
import os
import unittest
from unittest.mock import patch

from benchmarks.client import Client, Response
from benchmarks.database import Database
from benchmarks.fixtures import beatmap, create_match, packet, score_frame, spectator_frames, hardware
from benchmarks.report import Series
from protocol import decode_semantic_packet_stream


class BenchmarkContractTests(unittest.TestCase):
    def test_fixture_packets_use_the_existing_wire_decoder(self):
        fixture = beatmap(0)
        for payload in (create_match(0, 0, fixture), packet(47, score_frame(1)), spectator_frames(1)):
            decoded = decode_semantic_packet_stream(payload, "client")
            self.assertFalse(decoded.diagnostics)
        self.assertNotEqual(hardware(1), hardware(2))

    def test_targets_refuse_remote_or_non_fixture_databases(self):
        for origin in ("https://kai.ovh", "http://example.com:8080", "http://127.0.0.1:8080/path"):
            with self.assertRaises(ValueError):
                Client(origin)
        for dsn in ("postgresql://u:p@example.com/zigcho_benchmark", "postgresql://u:p@127.0.0.1/zigcho"):
            with patch.dict(os.environ, {"ZIGCHO_BENCHMARK_POSTGRES_URL": dsn}):
                with self.assertRaises(ValueError):
                    Database()

    def test_failed_responses_cannot_improve_successful_percentiles(self):
        series = Series()
        series.add(Response(status=200, elapsed_ms=250), True)
        for _ in range(100):
            series.add(Response(status=503, elapsed_ms=1), False)
        self.assertEqual(series.quantile(.95), 250)
        self.assertEqual(series.summary(10)["failed"], 100)


class BenchmarkHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_http_body_is_measured_not_treated_as_empty(self):
        async def serve(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        async with server:
            client = Client(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
            response = await client.request("GET", "/")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body, b"hello")
            self.assertEqual(client.active, 0)
