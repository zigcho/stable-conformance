"""Fixed-resolution, bounded client latency accounting and process sampling."""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

FAMILIES = ("poll", "chat", "spectator_host", "spectator_poll", "multiplayer", "score", "website", "replay")


class Series:
    def __init__(self):
        self.buckets = [0] * 10002  # 1 ms upper bounds through 10 s, then overflow.
        self.completed = 0
        self.success = 0
        self.missed = 0
        self.elapsed_sum_ms = 0.0
        self.max_ms = 0.0
        self.errors: dict[str, int] = {}

    def add(self, response, valid: bool) -> None:
        self.completed += 1
        self.elapsed_sum_ms += response.elapsed_ms
        self.max_ms = max(self.max_ms, response.elapsed_ms)
        if valid:
            self.success += 1
            self.buckets[min(10001, math.ceil(response.elapsed_ms))] += 1
        else:
            label = response.error or f"http_{response.status}" if response.status != 200 else "invalid_success_body"
            self.errors[label] = self.errors.get(label, 0) + 1

    def quantile(self, fraction: float) -> int | None:
        if not self.success:
            return None
        target = math.ceil(self.success * fraction)
        count = 0
        for index, value in enumerate(self.buckets):
            count += value
            if count >= target:
                return index if index < 10001 else None
        return None

    def summary(self, seconds: float) -> dict:
        return {
            "completed": self.completed, "successful": self.success, "failed": self.completed - self.success,
            "missed_schedule_slots": self.missed, "completed_rps": self.completed / seconds,
            "successful_rps": self.success / seconds, "errors": self.errors,
            "successful_p50_ms_upper": self.quantile(.5), "successful_p95_ms_upper": self.quantile(.95),
            "successful_p99_ms_upper": self.quantile(.99), "successful_over_10s": self.buckets[-1],
            "all_response_mean_ms": self.elapsed_sum_ms / self.completed if self.completed else None,
            "all_response_max_ms": self.max_ms, "resolution_ms": 1,
        }


def process_sample(pid: int) -> dict:
    result = {"monotonic_seconds": time.monotonic()}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM", "Threads"}:
                result[key] = int(value.split()[0])
        tail = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        result["cpu_seconds"] = (int(tail[11]) + int(tail[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        result["process_sample_unavailable"] = True
    return result


def parse_metrics(body: bytes) -> dict[str, float]:
    if len(body) > 128 * 1024:
        raise ValueError("oversized metrics response")
    result = {}
    for line in body.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.rsplit(" ", 1)
        result[key] = float(value)
        if len(result) > 2000:
            raise ValueError("unbounded metrics label set")
    return result


def window_metrics(before: dict, after: dict) -> dict:
    operations = {}
    for key, final in after.items():
        if not key.startswith('zigcho_duration_seconds_count{operation="'):
            continue
        name = key.split('"')[1]
        count = final - before.get(key, 0)
        buckets = []
        prefix = f'zigcho_duration_seconds_bucket{{operation="{name}",le="'
        for bucket_key, value in after.items():
            if bucket_key.startswith(prefix):
                bound = float(bucket_key[len(prefix):].split('"')[0])
                buckets.append((bound, value - before.get(bucket_key, 0)))
        buckets.sort()
        def quantile(fraction):
            if count <= 0:
                return None
            for bound, value in buckets:
                if value >= math.ceil(count * fraction):
                    return bound if math.isfinite(bound) else "overflow"
            return "inconsistent_snapshot"
        operations[name] = {"count": count, "p50_seconds_upper": quantile(.5), "p95_seconds_upper": quantile(.95), "p99_seconds_upper": quantile(.99), "counter_reset": count < 0}
    return operations
