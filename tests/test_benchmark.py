import asyncio
import base64
import hashlib
import lzma
import os
import sys
import unittest
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.client import Client, Response
from benchmarks.database import Database
from benchmarks.fixtures import beatmap, create_match, packet, score_frame, spectator_frames, hardware, score_request, VERSION, PASSWORD_MD5
from benchmarks.report import Series
from protocol import decode_semantic_packet_stream


class BenchmarkContractTests(unittest.TestCase):
    def test_score_multipart_matches_the_pinned_submission_contract(self):
        # Identity cipher isolates the actual multipart/checksum builder without
        # requiring the optional benchmark crypto package in stdlib-only checks.
        cipher = SimpleNamespace(encrypt=lambda value: value)
        crypto = SimpleNamespace(RijndaelCbc=lambda **kwargs: cipher, Pkcs7Padding=lambda size: size)
        with patch.dict(sys.modules, {"py3rijndael": crypto}):
            body, content_type, checksum, replay_sha = score_request(18, 1, beatmap(0))
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
        self.assertFalse(message.defects)
        fields = {}
        scores = []
        for part in message.iter_parts():
            name = part.get_param("name", header="Content-Disposition")
            if name == "score":
                scores.append(part)
            else:
                self.assertNotIn(name, fields)
                fields[name] = part.get_payload(decode=True)
        self.assertEqual(set(fields), {"x", "ft", "fs", "bmk", "sbk", "iv", "c1", "st", "pass", "osuver", "s"})
        self.assertEqual(fields["x"], b"0")
        self.assertEqual(fields["ft"], b"0")
        self.assertGreater(int(fields["st"]), 0)
        self.assertTrue(base64.b64decode(fields["fs"], validate=True))
        self.assertEqual(len(base64.b64decode(fields["iv"], validate=True)), 32)
        self.assertEqual(fields["pass"].decode(), PASSWORD_MD5)
        self.assertEqual(fields["osuver"].decode(), VERSION)
        client_hash = base64.b64decode(fields["s"], validate=True).decode()
        self.assertEqual(client_hash, hardware(18))
        ids = fields["c1"].split(b"|")
        self.assertEqual([hashlib.md5(value).hexdigest() for value in ids], client_hash.split(":")[3:5])
        self.assertEqual(len(scores), 2)
        self.assertIsNone(scores[0].get_filename())
        self.assertEqual(scores[1].get_filename(), "fixture.osr")
        values = base64.b64decode(scores[0].get_payload(decode=True), validate=True).decode().split(":")
        self.assertEqual(len(values), 18)
        self.assertEqual(values[0].encode(), fields["bmk"])
        self.assertEqual(values[2], checksum)
        # Independent reconstruction of bancho.py's online checksum field order.
        expected = (f"chickenmcnuggets{int(values[3]) + int(values[4])}o15{values[5]}{values[6]}"
                    f"smustard{values[7]}{values[8]}uu{values[0]}{values[10]}{values[11]}"
                    f"{values[1]}{values[9]}{values[12]}{values[13]}Q{values[14]}{values[15]}"
                    f"{VERSION}{values[16]}{client_hash}{fields['sbk'].decode()}")
        self.assertEqual(hashlib.md5(expected.encode()).hexdigest(), checksum)
        replay_bytes = scores[1].get_payload(decode=True)
        self.assertEqual(hashlib.sha256(replay_bytes).hexdigest(), replay_sha)
        self.assertIn(b"-12345|", lzma.decompress(replay_bytes))

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
