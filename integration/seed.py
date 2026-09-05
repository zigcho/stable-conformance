"""Matching synthetic database records for the disposable two-server runner."""

import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

from benchmarks import fixtures as f
from benchmarks.database import Database

ROLES = {
    "primary": 0, "peer": 1, "mp_primary": 2, "mp_peer": 3,
    "mp_invitee": 4, "host": 5, "spectator": 6, "tournament": 7,
    "malformed_1": 8, "malformed_2": 9, "malformed_3": 10,
    "malformed_4": 11, "restricted": 12, "restricted_observer": 13,
    "silenced": 14, "silenced_peer": 15, "web": 16,
    "login": 17, "delayed": 18, "tournament_host": 19,
}


def mysql(query: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", "-e", "MYSQL_PWD=conformance-fixture",
         "zigcho-conformance-mysql", "mysql", "-uroot", "--batch", "--skip-column-names",
         "bancho_conformance"],
        input=query, text=True, capture_output=True, timeout=60,
    )
    if result.returncode:
        # All input is synthetic, but keep SQL/credentials out of public logs.
        raise RuntimeError("isolated MySQL fixture operation failed")
    return result.stdout.strip()


def seed(reference: Path, reference_python: Path) -> dict:
    database = Database()  # Enforces loopback and the disposable database name.
    database.seed(accounts=20, scores=1, maps=20, active=20)
    silence_end = int(time.time()) + 3600
    database.sql(f"UPDATE zigcho.users SET privileges=19 WHERE id BETWEEN 10000 AND 10019; UPDATE zigcho.users SET restricted=true WHERE id=10012; UPDATE zigcho.users SET silence_end={silence_end} WHERE id=10014;")
    raw_replay = f.replay(0, 0, f.beatmap(0))
    database.sql(f"UPDATE zigcho.scores SET replay=decode('{raw_replay.hex()}','hex') WHERE id=1;")

    # Use the reference's pinned bcrypt dependency, never install it on the Mac.
    bcrypt_hash = subprocess.check_output(
        [str(reference_python), "-c", "import bcrypt,sys; print(bcrypt.hashpw(sys.stdin.buffer.read(),bcrypt.gensalt()).decode())"],
        input=f.PASSWORD_MD5.encode(), timeout=15,
    ).decode().strip()
    if len(bcrypt_hash) != 60 or any(c not in "./$0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in bcrypt_hash):
        raise ValueError("invalid generated fixture credential")
    mysql((reference / "migrations/base.sql").read_text())
    mysql("UPDATE users SET name='kai',safe_name='kai',country='au' WHERE id=1; DELETE FROM channels; INSERT INTO channels(name,topic,read_priv,write_priv,auto_join) VALUES ('#osu','general',1,2,1),('#announce','updates',1,24576,1),('#lobby','multiplayer lobby',1,2,0);")
    queries = []
    for index in range(20):
        priv = 0 if index == 12 else 19
        silenced = silence_end if index == 14 else 0
        queries.append(f"INSERT INTO users(id,name,safe_name,email,pw_bcrypt,priv,country,silence_end,creation_time,latest_activity) VALUES ({f.USER_BASE+index},'bench_{index}','bench_{index}','bench_{index}@example.invalid','{bcrypt_hash}',{priv},'au',{silenced},1700000000,1700000000);")
        for mode in (0, 1, 2, 3, 4, 5, 6, 8):
            queries.append(f"INSERT INTO stats(id,mode) VALUES ({f.USER_BASE+index},{mode});")
    map_dir = reference / ".data/osu"
    replay_dir = reference / ".data/osr"
    map_dir.mkdir(parents=True, exist_ok=True)
    replay_dir.mkdir(parents=True, exist_ok=True)
    # Medals' image files are outside this corpus. Avoid the reference startup's
    # bulk external asset download; achievement evaluation remains unchanged.
    (reference / ".data/assets/medals/client").mkdir(parents=True, exist_ok=True)
    for index in range(20):
        map_ = f.beatmap(index)
        (map_dir / f"{map_.id}.osu").write_bytes(map_.data)
        filename = f"zigcho benchmark - isolated workload {index} (bench_0) [synthetic].osu"
        queries.append(f"INSERT INTO mapsets(id) VALUES ({map_.id}); INSERT INTO maps(id,set_id,status,md5,artist,title,version,creator,filename,last_update,total_length,max_combo,frozen,mode,bpm,cs,ar,od,hp) VALUES ({map_.id},{map_.id},2,'{map_.md5}','zigcho benchmark','isolated workload {index}','synthetic','bench_0','{filename}',NOW(),121,600,1,0,150,4,8,6,5);")
    map_ = f.beatmap(0)
    checksum = hashlib.md5(b"historical-0").hexdigest()
    timestamp = database.sql("SELECT submitted_at FROM zigcho.scores WHERE id=1;")
    queries.append(f"INSERT INTO scores(id,map_md5,score,pp,acc,max_combo,mods,n300,n100,n50,nmiss,ngeki,nkatu,grade,status,mode,play_time,time_elapsed,client_flags,userid,perfect,online_checksum) VALUES (1,'{map_.md5}',1000000,20,100,600,0,600,0,0,0,0,0,'X',2,0,FROM_UNIXTIME({int(timestamp)}),121000,0,10000,1,'{checksum}'); UPDATE stats SET tscore=1000000,rscore=1000000,pp=20,acc=100,plays=1,playtime=121,max_combo=600,total_hits=600 WHERE id=10000 AND mode=0;")
    mysql("\n".join(queries))
    (replay_dir / "1.osr").write_bytes(raw_replay)
    subprocess.run(["docker", "exec", "zigcho-conformance-redis", "redis-cli", "ZADD", "bancho:leaderboard:0", "20", "10000"], check=True, capture_output=True, timeout=10)
    snapshot = {"schema": 1, "roles": ROLES, "maps": [{"id": f.beatmap(i).id, "md5": f.beatmap(i).md5} for i in range(20)],
                "score": {"id": 1, "score": 1000000, "pp": 20, "replay_sha256": hashlib.sha256(raw_replay).hexdigest()},
                "reference_bot_id": 1, "zigcho_bot_id": 3,
                "bot_memberships": ["#osu", "#announce"],
                "reference_bootstrap": "real lifespan plus bot.join_channel fixture calls; packet handlers unchanged",
                "asset_scope": "medal image downloads excluded; image routes are not covered",
                "note": "Bot ids differ by product contract; comparisons do not erase them. No real users or captures are used."}
    return snapshot


def screenshot(index: int):
    # One transparent PNG, with the same bytes on both servers.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhGQAAAAASUVORK5CYII=")
    boundary = "conformance-screenshot"
    parts = []
    for name, value in (("u", f.username(index).encode()), ("p", f.PASSWORD_MD5.encode()), ("v", b"1"), ("ss", png)):
        filename = '; filename="fixture.png"' if name == "ss" else ""
        mime = "Content-Type: image/png\r\n" if name == "ss" else ""
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{filename}\r\n{mime}\r\n'.encode() + value + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), "multipart/form-data; boundary=" + boundary
