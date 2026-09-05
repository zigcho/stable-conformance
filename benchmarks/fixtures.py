"""Synthetic maps and valid Stable wire payloads using the existing decoder contract."""

from __future__ import annotations

import base64
import hashlib
import lzma
import random
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from protocol import HEADER
from transcript import encode_body

PASSWORD_MD5 = hashlib.md5(b"isolated zigcho benchmark fixture").hexdigest()
VERSION = "20260905"
USER_BASE = 10000
MAP_BASE = 1100000000


def username(index: int) -> str:
    return f"bench_{index}"


def hardware(index: int) -> str:
    def digest(part: str) -> str:
        return hashlib.md5(f"benchmark:{index}:{part}".encode()).hexdigest()
    return f"{digest('path')}:bench{index}.:{digest('adapter')}:{digest('install')}:{digest('disk')}:"


def login(index: int) -> bytes:
    return f"{username(index)}\n{PASSWORD_MD5}\nb{VERSION}|0|0|{hardware(index)}|0".encode()


def packet(packet_id: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(packet_id, 0, len(payload)) + payload


def string(value: str) -> bytes:
    return encode_body({"encoding": "osu_string", "value": value}, {}) or b"\x00"


@dataclass(frozen=True)
class Map:
    index: int
    data: bytes
    md5: str
    objects: int
    duration_ms: int

    @property
    def id(self) -> int:
        return MAP_BASE + self.index


def beatmap(index: int, objects: int = 600) -> Map:
    if not 1 <= objects <= 4000 or not 0 <= index < 10000:
        raise ValueError("fixture map bounds exceeded")
    header = (
        "osu file format v14\n\n[General]\nAudioFilename: fixture.mp3\nMode: 0\n"
        f"\n[Metadata]\nTitle: isolated workload {index}\nArtist: zigcho benchmark\n"
        "Creator: bench_0\nVersion: synthetic\n"
        f"BeatmapID: {MAP_BASE + index}\nBeatmapSetID: {MAP_BASE + index}\n"
        "\n[Difficulty]\nHPDrainRate:5\nCircleSize:4\nOverallDifficulty:6\n"
        "ApproachRate:8\nSliderMultiplier:1.4\nSliderTickRate:1\n"
        "\n[TimingPoints]\n0,400,4,2,1,60,1,0\n\n[HitObjects]\n"
    )
    notes = "".join(f"{64 + (n * 73) % 384},{64 + (n * 41) % 256},{1000 + n * 200},1,0,0:0:0:0:\n" for n in range(objects))
    data = (header + notes).encode()
    return Map(index, data, hashlib.md5(data).hexdigest(), objects, 1000 + objects * 200)


def replay(index: int, sequence: int, map_: Map) -> bytes:
    rng = random.Random(index * 1000003 + sequence)
    frames = ["0|256|192|0,"]
    previous = 0
    for n in range(map_.objects):
        at = 1000 + n * 200 + rng.randint(-3, 3)
        x = 64 + (n * 73) % 384 + rng.randint(-2, 2)
        y = 64 + (n * 41) % 256 + rng.randint(-2, 2)
        frames.append(f"{at - 10 - previous}|{x}|{y}|0,10|{x}|{y}|4,10|{x}|{y}|0,")
        previous = at + 10
    frames.append(f"-12345|0|0|{rng.randrange(1, 2**31)},")
    return lzma.compress("".join(frames).encode(), format=lzma.FORMAT_ALONE, preset=1)


def score_request(index: int, sequence: int, map_: Map) -> tuple[bytes, str, str, str]:
    # Optional, hash-pinned benchmark dependency; normal harness checks remain stdlib-only.
    from py3rijndael import RijndaelCbc, Pkcs7Padding

    client_time = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    total = 9000000 + index * 37 + sequence
    n = map_.objects
    hash_input = f"chickenmcnuggets{n}o1500smustard00uu{map_.md5}{n}True{username(index)}{total}X0QTrue0{VERSION}{client_time}{hardware(index)}"
    checksum = hashlib.md5(hash_input.encode()).hexdigest()
    plaintext = f"{map_.md5}:{username(index)}:{checksum}:{n}:0:0:0:0:0:{total}:{n}:True:X:0:True:0:{client_time}:0"
    iv = hashlib.sha256(f"benchmark:{index}:{sequence}".encode()).digest()
    key = b"osu!-scoreburgr---------" + VERSION.encode()

    def encrypted(value: str) -> bytes:
        cipher = RijndaelCbc(key=key, iv=iv, padding=Pkcs7Padding(32), block_size=32)
        return base64.b64encode(cipher.encrypt(value.encode()))

    replay_bytes = replay(index, sequence, map_)
    boundary = f"zigcho-benchmark-{checksum}".encode()
    parts: list[bytes] = []

    def part(name: str, data: bytes, filename: str | None = None) -> None:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        parts.append(b"--" + boundary + b"\r\n" + disposition.encode() + b"\r\n\r\n" + data + b"\r\n")

    part("score", encrypted(plaintext))
    part("score", replay_bytes, "fixture.osr")
    for name, value in {
        "iv": base64.b64encode(iv), "s": encrypted(hardware(index)),
        "pass": PASSWORD_MD5.encode(), "osuver": VERSION.encode(),
        "bmk": map_.md5.encode(), "sbk": b"", "st": str(map_.duration_ms).encode(), "ft": b"0",
    }.items():
        part(name, value)
    body = b"".join(parts) + b"--" + boundary + b"--\r\n"
    return body, "multipart/form-data; boundary=" + boundary.decode(), checksum, hashlib.sha256(replay_bytes).hexdigest()


def message(index: int, sequence: int) -> bytes:
    return packet(1, string(username(index)) + string(f"benchmark message {sequence}") + string("#osu") + struct.pack("<i", USER_BASE + index))


def create_match(index: int, room: int, map_: Map) -> bytes:
    payload = struct.pack("<hBBi", 0, 0, 0, 0)
    payload += string(f"benchmark room {room}") + string("") + string("zigcho benchmark - isolated workload [synthetic]")
    payload += struct.pack("<i", map_.id) + string(map_.md5)
    payload += bytes([4] + [1] * 15) + bytes(16)
    payload += struct.pack("<iiBBBBi", USER_BASE + index, USER_BASE + index, 0, 0, 0, 0, 0)
    return packet(31, payload)


def score_frame(sequence: int) -> bytes:
    return struct.pack("<iB6HiHHBBBB", sequence * 100, 0, sequence % 600, 0, 0, 0, 0, 0, sequence * 1000, sequence % 600, sequence % 600, 1, 255, 0, 0)


def spectator_frames(sequence: int) -> bytes:
    frames = b"".join(struct.pack("<BBffi", 0, 0, float(200 + n), float(150 + n), sequence * 100 + n * 16) for n in range(6))
    return packet(18, struct.pack("<iH", 0, 6) + frames + b"\x00" + score_frame(sequence) + struct.pack("<H", sequence % 65536))
