"""Reproducible mixed Stable workload against a disposable, marked database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import re
import struct
import time
from pathlib import Path
from urllib.parse import urlencode

from benchmarks.client import Client, Response
from benchmarks.database import Database
from benchmarks import fixtures as f
from benchmarks.report import FAMILIES, Series, parse_metrics, process_sample, window_metrics
from protocol import decode_packet_stream


class Workload:
    def __init__(self, args):
        self.args = args
        self.client = Client(args.origin)
        self.database = Database()
        self.tokens: list[str] = [""] * args.players
        self.maps = [f.beatmap(index) for index in range(args.maps)]
        self.received = {"spectator_frames": 0, "multiplayer_scores": 0, "chat_messages": 0, "stats_updates": 0}
        self.score_checksums: list[str] = []
        self.score_ids: list[int] = []
        self.room_members: set[int] = set()
        self.spectator_hosts: set[int] = set()
        self.spectators: set[int] = set()
        self.preflight_score_id: int | None = None
        self.bootstrap_progress = {"logged_in": 0, "keepalive_failures": 0}

    def packets_valid(self, response: Response) -> bool:
        if response.error or response.status != 200:
            return False
        decoded = decode_packet_stream(response.body)
        if decoded.diagnostics:
            return False
        for packet in decoded.packets:
            if packet.packet_id == 5 and len(packet.payload) >= 4 and struct.unpack_from("<i", packet.payload)[0] < 0:
                return False
            if packet.packet_id == 104:
                return False
            if packet.packet_id == 15:
                self.received["spectator_frames"] += 1
            if packet.packet_id == 48:
                self.received["multiplayer_scores"] += 1
            if packet.packet_id == 7 and b"benchmark message" in packet.payload:
                self.received["chat_messages"] += 1
            if packet.packet_id == 11:
                self.received["stats_updates"] += 1
        return True

    async def poll(self, index: int, body: bytes = b"") -> Response:
        return await self.client.request("POST", "/", body, {"osu-token": self.tokens[index]}, player=index)

    async def bootstrap(self) -> dict:
        semaphore = asyncio.Semaphore(8)
        login_latencies = Series()
        next_poll = [0.0] * self.args.players

        async def one(index):
            async with semaphore:
                response = await self.client.request("POST", "/", f.login(index), player=index)
                token = response.headers.get("osu-token") or response.headers.get("cho-token", "")
                valid = self.packets_valid(response) and bool(re.fullmatch("[0-9a-f]{64}", token))
                login_latencies.add(response, valid)
                if not valid:
                    raise RuntimeError(f"fixture login failed at player {index}, status={response.status}, error={response.error}")
                self.tokens[index] = token
                next_poll[index] = time.monotonic() + (index % 50) / 10
                self.bootstrap_progress["logged_in"] += 1
                if self.bootstrap_progress["logged_in"] % 100 == 0:
                    print(json.dumps({"stage": "login", **self.bootstrap_progress}), flush=True)

        started = time.monotonic()
        done = asyncio.Event()

        async def keepalive():
            while not done.is_set():
                async def check(index):
                    if not self.packets_valid(await self.poll(index)):
                        self.bootstrap_progress["keepalive_failures"] += 1
                now = time.monotonic()
                due = [index for index, token in enumerate(self.tokens) if token and next_poll[index] <= now]
                for index in due:
                    next_poll[index] = now + 5
                await asyncio.gather(*(check(index) for index in due))
                try:
                    await asyncio.wait_for(done.wait(), .1)
                except TimeoutError:
                    pass

        # Already-connected clients continue polling during a slow login ramp.
        # Otherwise the five-minute expiry silently reduces the target population.
        heartbeat = asyncio.create_task(keepalive())
        try:
            async with asyncio.timeout(600):
                await asyncio.gather(*(one(index) for index in range(self.args.players)))
        finally:
            done.set()
            await heartbeat
        if self.bootstrap_progress["keepalive_failures"]:
            raise RuntimeError("a connected fixture client was lost during the login ramp")
        result = login_latencies.summary(time.monotonic() - started)
        result["setup_seconds"] = time.monotonic() - started

        # A real score and identical retry must work before generating load.
        score = await asyncio.to_thread(f.score_request, self.args.players - 1, 0, self.maps[-1])
        first = await self.submit(self.args.players - 1, score)
        retry = await self.submit(self.args.players - 1, score)
        if not first[1] or not retry[1] or first[2] != retry[2]:
            raise RuntimeError(f"valid score/retry preflight failed, statuses={first[0].status}/{retry[0].status}")
        self.preflight_score_id = first[2]
        stored = self.database.verify_scores([score[2]])
        if stored["stored"] != 1 or stored["with_replay"] != 1 or stored["archived"] != 1:
            raise RuntimeError("score preflight did not persist and archive one canonical score with replay")
        result["idempotent_score_retry"] = stored

        for host in range(5):
            self.spectator_hosts.add(host)
            for offset in range(3):
                viewer = 5 + host * 3 + offset
                self.spectators.add(viewer)
                if not self.packets_valid(await self.poll(viewer, f.packet(16, struct.pack("<i", f.USER_BASE + host)))):
                    raise RuntimeError("spectator setup failed")

        for room in range(4):
            host = 20 + room * 4
            response = await self.poll(host, f.create_match(host, room, self.maps[room]))
            decoded = decode_packet_stream(response.body)
            joined = next((packet for packet in decoded.packets if packet.packet_id == 36), None)
            if not self.packets_valid(response) or joined is None:
                raise RuntimeError("multiplayer create preflight failed")
            room_id = struct.unpack_from("<h", joined.payload)[0]
            for index in range(host + 1, host + 4):
                response = await self.poll(index, f.packet(32, struct.pack("<i", room_id) + f.string("")))
                if not self.packets_valid(response) or not any(packet.packet_id == 36 for packet in decode_packet_stream(response.body).packets):
                    raise RuntimeError("multiplayer join preflight failed")
            for index in range(host, host + 4):
                self.room_members.add(index)
                await self.poll(index, f.packet(39))
            await self.poll(host, f.packet(44, b"\x00"))
            for index in range(host, host + 4):
                await self.poll(index, f.packet(52))
        return result

    async def submit(self, index: int, prepared) -> tuple[Response, bool, int | None]:
        body, content_type, _, _ = prepared
        response = await self.client.request("POST", "/web/osu-submit-modular-selector.php", body, {"Content-Type": content_type, "token": self.tokens[index]}, player=index)
        found = re.search(rb"onlineScoreId:(\d+)", response.body)
        valid = response.status == 200 and response.error is None and found is not None
        return response, valid, int(found.group(1)) if found else None

    async def metrics(self) -> dict:
        response = await self.client.request("GET", "/metrics/runtime", headers={"Host": "127.0.0.1", "User-Agent": "zigcho-benchmark"})
        if response.status != 200 or response.error:
            raise RuntimeError("runtime metrics unavailable")
        return parse_metrics(response.body)

    async def phase(self, name: str, sequence_base: int) -> dict:
        series = {family: Series() for family in FAMILIES}
        received_before = self.received.copy()
        self.score_checksums = []
        self.score_ids = [self.preflight_score_id] if self.preflight_score_id is not None else []
        samples = []
        self.database.reset_statements()
        before = await self.metrics()
        started = time.monotonic()
        deadline = started + self.args.seconds
        max_lateness = 0.0
        sampling_done = asyncio.Event()

        async def periodic(family, identity, period, operation):
            nonlocal max_lateness
            rng = random.Random(sequence_base + identity * 7919 + FAMILIES.index(family))
            due = started + rng.random() * period
            sequence = 0
            while due < deadline:
                await asyncio.sleep(max(0, due - time.monotonic()))
                max_lateness = max(max_lateness, max(0, time.monotonic() - due) * 1000)
                response, valid = await operation(sequence)
                series[family].add(response, valid)
                sequence += 1
                due += period
                now = time.monotonic()
                if now > due:
                    skipped = int((min(now, deadline) - due) // period) + 1 if due < deadline else 0
                    series[family].missed += skipped
                    due += skipped * period

        async def polling(index, sequence):
            response = await self.poll(index)
            return response, self.packets_valid(response)

        async def chatting(index, sequence):
            response = await self.poll(index, f.message(index, sequence + sequence_base))
            return response, self.packets_valid(response)

        async def spectating(index, sequence):
            response = await self.poll(index, f.spectator_frames(sequence + sequence_base))
            return response, self.packets_valid(response)

        async def multiplayer(index, sequence):
            response = await self.poll(index, f.packet(47, f.score_frame(sequence)))
            return response, self.packets_valid(response)

        async def scoring(index, sequence):
            prepared = await asyncio.to_thread(f.score_request, index, sequence_base + sequence + 1, self.maps[(index + sequence) % len(self.maps)])
            response, valid, score_id = await self.submit(index, prepared)
            if valid:
                if len(self.score_checksums) >= 20000:
                    raise RuntimeError("score receipt evidence bound exceeded")
                self.score_checksums.append(prepared[2])
                self.score_ids.append(score_id)
            return response, valid

        async def website(index, sequence):
            paths = [f"/api/v1/users/{f.USER_BASE + (index + sequence) % self.args.players}", "/api/v1/rankings?mode=0", f"/api/v1/beatmaps/{self.maps[sequence % len(self.maps)].id}/leaderboard?mode=0"]
            response = await self.client.request("GET", paths[sequence % 3], headers={"Host": "kai.ovh", "User-Agent": "zigcho-benchmark"}, player=self.args.players + index)
            try:
                body = json.loads(response.body)
                valid = response.status == 200 and isinstance(body, (dict, list)) and bool(body) and not (isinstance(body, dict) and "error" in body)
            except (ValueError, UnicodeError):
                valid = False
            return response, valid

        async def replaying(index, sequence):
            if not self.score_ids:
                # No fake success is recorded while waiting for the first receipt.
                return Response(error="no_score_receipt"), False
            score_id = self.score_ids[sequence % len(self.score_ids)]
            query = urlencode({"u": f.username(index), "h": f.PASSWORD_MD5, "c": score_id})
            response = await self.client.request("GET", "/web/osu-getreplay.php?" + query, player=index)
            return response, response.status == 200 and bool(response.body) and response.error is None

        async def sample():
            while not sampling_done.is_set():
                row = process_sample(self.args.server_pid)
                try:
                    metrics = await self.metrics()
                    row["gauges"] = {key: value for key, value in metrics.items() if key.startswith(("zigcho_work_", "zigcho_http_"))}
                except RuntimeError:
                    row["metrics_unavailable"] = True
                samples.append(row)
                try:
                    await asyncio.wait_for(sampling_done.wait(), 5)
                except TimeoutError:
                    pass

        tasks = []
        for index in range(self.args.players):
            if index in self.spectator_hosts:
                tasks.append(periodic("spectator_host", index, .1, lambda n, i=index: spectating(i, n)))
            elif index in self.spectators:
                tasks.append(periodic("spectator_poll", index, .1, lambda n, i=index: polling(i, n)))
            elif index in self.room_members:
                tasks.append(periodic("multiplayer", index, .2, lambda n, i=index: multiplayer(i, n)))
            else:
                tasks.append(periodic("poll", index, 5, lambda n, i=index: polling(i, n)))
        for index in range(40, min(90, self.args.players)):
            tasks.append(periodic("chat", index, 30, lambda n, i=index: chatting(i, n)))
        for index in range(40, min(self.args.players, 40 + self.args.players * 3 // 5)):
            tasks.append(periodic("score", index, 180, lambda n, i=index: scoring(i, n)))
        for index in range(20):
            tasks.append(periodic("website", index, 1, lambda n, i=index: website(i, n)))
        for index in range(2):
            tasks.append(periodic("replay", index + 90, 10, lambda n, i=index + 90: replaying(i, n)))
        sampler = asyncio.create_task(sample())
        try:
            await asyncio.gather(*tasks)
        finally:
            sampling_done.set()
            await sampler
        finished = time.monotonic()
        after = await self.metrics()
        top_queries = self.database.statements()
        verification = self.database.verify_scores(self.score_checksums)
        verification["spectator_frame_deliveries"] = self.received["spectator_frames"] - received_before["spectator_frames"]
        verification["multiplayer_score_deliveries"] = self.received["multiplayer_scores"] - received_before["multiplayer_scores"]
        verification["chat_message_deliveries"] = self.received["chat_messages"] - received_before["chat_messages"]
        verification["authoritative_ok"] = verification["stored"] == verification["acknowledged"] == verification["with_replay"] and verification["duplicate_best_scopes"] == 0
        verification["archival_ok"] = verification["archived"] == verification["acknowledged"]
        verification["ok"] = verification["authoritative_ok"] and verification["archival_ok"] and all(verification[key] > 0 for key in ("spectator_frame_deliveries", "multiplayer_score_deliveries", "chat_message_deliveries"))
        return {"name": name, "scheduled_seconds": self.args.seconds, "elapsed_seconds": finished - started, "players": self.args.players, "maximum_scheduler_lateness_ms": max_lateness, "peak_generator_requests": self.client.peak, "families": {family: value.summary(finished - started) for family, value in series.items()}, "server_duration_windows": window_metrics(before, after), "resource_samples": samples, "correctness": verification, "database": self.database.size(), "top_queries": top_queries}


async def main(args):
    if not args.allow_local_mutations:
        raise ValueError("--allow-local-mutations is required")
    if not 100 <= args.players <= 2000 or not 30 <= args.seconds <= 3600:
        raise ValueError("players must be 100..2000 and each phase 30..3600 seconds")
    workload = Workload(args)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    if args.command == "seed":
        body = urlencode({"name": "bench_seed", "email": "bench_seed@example.invalid", "password_md5": f.PASSWORD_MD5}).encode()
        response = await workload.client.request("POST", "/users", body, {"Content-Type": "application/x-www-form-urlencoded"})
        if response.status != 201:
            raise RuntimeError(f"fixture registration failed: {response.status}")
        report = workload.database.seed(args.accounts, args.historical_scores, args.maps, args.players)
    else:
        if not re.fullmatch("[0-9a-f]{40}", args.server_commit) or args.server_pid <= 0:
            raise ValueError("an exact server commit and running PID are required")
        if workload.database.sql("SELECT name FROM public.zigcho_benchmark_marker;") != "isolated-fixture-v1":
            raise RuntimeError("isolated fixture marker is missing")
        with open(f"/proc/{args.server_pid}/exe", "rb") as executable:
            executable_hash = hashlib.file_digest(executable, "sha256").hexdigest()
        if executable_hash != args.server_sha256:
            raise RuntimeError("running executable does not match the recorded artifact")
        report = {"schema": 1, "server_commit": args.server_commit, "server_sha256": executable_hash, "hardware": {"platform": platform.platform(), "logical_cpus": os.cpu_count(), "cpuinfo": Path("/proc/cpuinfo").read_text().split("\n\n", 1)[0]}, "scope": "synthetic Stable osu!standard vanilla clients, direct-origin HTTP, local HTTPS MinIO; no production or private anticheat module", "cache_scope": "first gameplay pass after restart and database-cache reset, then the same process/sessions warm; login necessarily warms account state", "phases": []}
        bootstrap_before = await workload.metrics()
        workload.database.reset_statements()
        try:
            report["bootstrap"] = await workload.bootstrap()
            report["bootstrap_durations"] = window_metrics(bootstrap_before, await workload.metrics())
            report["bootstrap_queries"] = workload.database.statements()
            for name, sequence in (("cold_gameplay", 10000), ("warm", 20000)):
                print(json.dumps({"stage": name, "seconds": args.seconds}), flush=True)
                report["phases"].append(await workload.phase(name, sequence))
                Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        except Exception as error:
            report["incomplete"] = True
            report["failure_type"] = type(error).__name__
            report["bootstrap_progress"] = workload.bootstrap_progress
            try:
                report["failure_durations"] = window_metrics(bootstrap_before, await workload.metrics())
                report["failure_queries"] = workload.database.statements()
            except Exception:
                report["failure_metrics_unavailable"] = True
            Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
            raise
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"command": args.command, "output": args.output, "players": args.players, "phases": len(report.get("phases", []))}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed", "run"))
    parser.add_argument("--origin", default="http://127.0.0.1:18090")
    parser.add_argument("--players", type=int, default=1000)
    parser.add_argument("--accounts", type=int, default=10000)
    parser.add_argument("--historical-scores", type=int, default=100000)
    parser.add_argument("--maps", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--server-pid", type=int, default=0)
    parser.add_argument("--server-commit", default="")
    parser.add_argument("--server-sha256", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-local-mutations", action="store_true")
    asyncio.run(main(parser.parse_args()))
