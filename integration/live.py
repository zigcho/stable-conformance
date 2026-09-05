"""Run the complete corpus against two disposable real servers on GitHub.

No response rewriting, production credentials, synthetic success responses or
per-case skips. A failure remains a failure; setup actions are recorded separately.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from benchmarks import fixtures as f
from integration.proxy import start as start_proxy
from integration.mirror import start as start_mirror
from integration.seed import ROLES, screenshot, seed
from protocol import decode_packet_stream
from run import main as run_corpus

PIN = "0651b54c66daa839c1bb3998e4f9a8d1173e144d"
HERE = Path(__file__).resolve().parents[1]


def request(port, body=b"", token=None, path="/", content_type=None):
    headers = {"User-Agent": "osu!"}
    if token:
        headers["osu-token"] = token
    if content_type:
        headers["Content-Type"] = content_type
    req = Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers)
    try:
        with urlopen(req, timeout=15) as response:
            return response.status, response.headers, response.read(16 * 1024 * 1024)
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read(1024 * 1024)


def packet_ids(body):
    decoded = decode_packet_stream(body)
    if not decoded.complete:
        raise RuntimeError("fixture boundary returned a malformed packet stream")
    return [packet.packet_id for packet in decoded.packets]


def build_config(ports, snapshot, commit):
    template = json.loads((HERE / "config.example.json").read_text())
    template["metadata"] = {
        "fixture": "hosted-stable-differential-" + os.environ["GITHUB_RUN_ID"],
        "fixture_reset_at": datetime.now(timezone.utc).isoformat(),
        "fixture_snapshot_sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
        "zigcho_commit": commit, "reference_commit": PIN,
    }
    template["variables"].update(client_version="b" + f.VERSION, search_query="isolated workload 0")
    score_payloads = {prefix: f.score_request(index, 1, f.beatmap(0))
                      for prefix, index in (("score", 16), ("delayed_score", 18))}
    all_tokens = {}
    for name, port in ports.items():
        tokens = {}
        values = {}
        for role, index in ROLES.items():
            if role not in {"login", "delayed"}:
                status, headers, data = request(port, f.login(index))
                token = headers.get("cho-token")
                if status != 200 or not token or token == "no":
                    raise RuntimeError(f"{name} rejected isolated login role {role}: HTTP {status}")
                tokens[role] = token
            prefix = "stable_" + role
            # Malformed fixtures use a suffix rather than role-first naming.
            for suffix, value in (("username", f.username(index)), ("user_id", f.USER_BASE + index),
                                  ("password_md5", f.PASSWORD_MD5), ("client_hashes", f.hardware(index))):
                key = prefix + "_" + suffix
                if role.startswith("malformed_"):
                    key = "stable_malformed_" + suffix + "_" + role.rsplit("_", 1)[1]
                values[key] = value
            if role in tokens:
                key = prefix + "_token" if not role.startswith("malformed_") else "stable_malformed_token_" + role.rsplit("_", 1)[1]
                values[key] = tokens[role]
        all_tokens[name] = tokens
        request(port, f.packet(30), tokens["mp_invitee"])
        if name == "reference":
            request(port, f.packet(63, f.string("#lobby")), tokens["mp_invitee"])
        map_ = f.beatmap(0)
        values.update(stable_bot_user_id=3 if name == "zigcho" else 1,
                      username=f.username(16), password_md5=f.PASSWORD_MD5, user_id=10016,
                      stable_token=tokens["web"], beatmap_id=map_.id, beatmap_set_id=map_.id,
                      beatmap_md5=map_.md5, beatmap_filename="zigcho benchmark - isolated workload 0 (bench_0) [synthetic].osu",
                      score_id=1, direct_message_sender=f.username(0), stable_mp_match_id=0,
                      stable_mp_match_password="fixture-room", stable_mp_match_password_updated="fixture-room-updated",
                      stable_tournament_match_id=0, stable_restricted_channel="#osu", stable_silenced_public_channel="#osu")
        for prefix, index in (("score", 16), ("delayed_score", 18)):
            body, content_type, _, _ = score_payloads[prefix]
            values[prefix + "_multipart_body_b64"] = base64.b64encode(body).decode()
            values[prefix + "_multipart_content_type"] = content_type
        body, content_type = screenshot(16)
        values.update(screenshot_multipart_body_b64=base64.b64encode(body).decode(), screenshot_multipart_content_type=content_type)
        target = template["targets"][name]
        missing = set(target["variables"]) - set(values)
        if missing:
            raise RuntimeError("unprovisioned fixture variables: " + ", ".join(sorted(missing)))
        target["variables"] = {key: {"value": values[key], "secret": spec.get("secret", False)}
                               for key, spec in target["variables"].items()}
        target["allow_mutating"] = True
        target["origin"] = f"http://127.0.0.1:{port}"
        for token in tokens.values():
            request(port, token=token)
        # The reference's rating endpoint intentionally consults its RAM map
        # cache. Establish that precondition through the real leaderboard route,
        # independently of whether the earlier read-only transcript passes.
        query = urlencode({"s": 0, "vv": 4, "v": 1, "c": map_.md5,
                           "f": values["beatmap_filename"], "m": 0, "i": map_.id,
                           "mods": 0, "h": "", "a": 0, "us": f.username(16), "ha": f.PASSWORD_MD5})
        status, _, _ = request(port, None, tokens["web"], path="/web/osu-osz2-getscores.php?" + query)
        if status != 200:
            raise RuntimeError(f"{name} could not warm the fixture map: HTTP {status}")
    # Login broadcasts from later accounts must not contaminate early sessions.
    for name, tokens in all_tokens.items():
        for token in tokens.values():
            request(ports[name], token=token)
    return template, all_tokens


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--zigcho-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("GITHUB_RUN_ID"):
        raise SystemExit("this runner is GitHub-only; no Mac or production execution")
    args.runtime.mkdir(parents=True, exist_ok=False)
    args.reports.mkdir(parents=True, exist_ok=True)
    reference = args.reference_root.resolve()
    reference_python = reference / ".venv/bin/python"
    commit = subprocess.check_output(["git", "-C", str(args.zigcho_root), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip() != PIN:
        raise SystemExit("reference checkout does not match the audited pin")
    (args.runtime / "config.ini").write_text((HERE / "benchmarks/config.ini").read_text())
    ports = {"zigcho": 18090, "reference": 18091}
    proxies = [start_proxy(18090, 18190, "kai.ovh"), start_proxy(18091, 18191, "fixture.invalid"), start_mirror()]
    processes = []
    logs = []

    def launch(command, cwd, name, env=None):
        log = (args.reports / (name + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def wait_ready(port, process, path="/"):
        for _ in range(45):
            if process.poll() is not None:
                raise RuntimeError(f"isolated server exited with {process.returncode}")
            try:
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
                    return
            except HTTPError as exc:
                if exc.code != 502:
                    return
            except (URLError, TimeoutError):
                pass
            time.sleep(1)
        raise RuntimeError("isolated server did not become ready within 45 seconds")

    try:
        zigcho = launch([str(args.binary.resolve()), "127.0.0.1", "18190"], args.runtime, "zigcho")
        wait_ready(18190, zigcho, "/metrics/runtime")
        status, _, _ = request(18090, urlencode({"name": "bench_seed", "email": "bench_seed@example.invalid", "password_md5": f.PASSWORD_MD5}).encode(), path="/users", content_type="application/x-www-form-urlencoded")
        if status != 201:
            raise RuntimeError(f"isolated registration failed: HTTP {status}")
        snapshot = seed(reference, reference_python)
        (reference / "logging.yaml").write_text((reference / "logging.yaml.example").read_text())
        (args.reports / "fixture.json").write_text(json.dumps(snapshot, indent=2) + "\n")
        env = os.environ.copy()
        # Audited reference test config; every endpoint/credential overridden below
        # is synthetic and local. No .env or repository source is modified.
        for line in (reference / ".env.test").read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
        env.update(APP_HOST="127.0.0.1", APP_PORT="18191", DOMAIN="fixture.invalid",
                   DB_HOST="127.0.0.1", DB_PORT="13306", DB_USER="root", DB_PASS="conformance-fixture", DB_NAME="bancho_conformance",
                   REDIS_HOST="127.0.0.1", REDIS_PORT="16379", REDIS_USER="", REDIS_PASS="", REDIS_DB="0",
                   DEBUG="False", DEVELOPER_MODE="False", SEASONAL_BGS="", OSU_API_KEY="", DISCORD_INVITE="",
                   MIRROR_SEARCH_ENDPOINT="http://127.0.0.1:18999/search", MIRROR_DOWNLOAD_ENDPOINT="http://127.0.0.1:18999/d",
                   MENU_ICON_URL="", MENU_ONCLICK_URL="", DISCORD_AUDIT_LOG_WEBHOOK="", AUTOMATICALLY_REPORT_PROBLEMS="False")
        reference_boot = HERE / "integration/reference_boot.py"
        bancho = launch([str(reference_python), str(reference_boot)], reference, "reference", env)
        wait_ready(18091, bancho)
        config, tokens = build_config(ports, snapshot, commit)
        config_path = args.runtime / "fixture-config.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)  # Contains only synthetic login credentials/tokens.

        def prepare(case_id, states):
            evidence = {"boundary": "before-case only; no in-case drains or response rewriting", "targets": {}}
            for name, port in ports.items():
                actions = []
                if case_id in {"packet-multiplayer", "packet-tournament"}:
                    for role in ("mp_primary", "mp_peer", "tournament_host"):
                        status, _, body = request(port, f.packet(33), tokens[name][role])
                        actions.append({"action": "leave-old-fixture-room", "role": role, "status": status, "packet_ids": packet_ids(body)})
                if case_id == "packet-tournament":
                    status, _, body = request(port, f.create_match(19, 0, f.beatmap(0)), tokens[name]["tournament_host"])
                    actions.append({"action": "create-tournament-fixture-room", "status": status, "packet_ids": packet_ids(body)})
                if case_id == "packet-session-presence-chat":
                    status, _, body = request(port, f.packet(78, f.string("#osu")), tokens[name]["primary"])
                    actions.append({"action": "establish-primary-not-joined-baseline", "status": status, "packet_ids": packet_ids(body)})
                drains = []
                for role, token in tokens[name].items():
                    status, _, body = request(port, token=token)
                    decoded = decode_packet_stream(body)
                    # Earlier malformed/logout scenarios may deliberately
                    # invalidate a dedicated fixture token. Record that traffic
                    # without mistaking it for the next scenario's response.
                    drains.append({"role": role, "status": status,
                                   "complete_packet_stream": decoded.complete,
                                   "packet_ids": [packet.packet_id for packet in decoded.packets],
                                   "body_sha256": hashlib.sha256(body).hexdigest()})
                evidence["targets"][name] = {"actions": actions, "drained": drains}
            return evidence

        (args.reports / "runtime-attestation.json").write_text(json.dumps({
            "zigcho_commit": commit, "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            "reference_commit": PIN, "zigcho_pid": zigcho.pid, "reference_pid": bancho.pid,
            "reference_bootstrap_sha256": hashlib.sha256(reference_boot.read_bytes()).hexdigest(),
            "zigcho_pp_engine": args.binary.with_name("pp-engine-version").read_text().strip(),
            "reference_pp_engine": "akatsuki-pp-py==1.0.5 (frozen reference dependency)",
            "pp_engine_boundary": "engines are not identical; numerical differences remain failures, never normalized",
            "scope": "synthetic isolated HTTP differential corpus; not real Stable client acceptance",
        }, indent=2) + "\n")
        return run_corpus(["run", "--config", str(config_path), "--zigcho-root", str(args.zigcho_root),
                           "--reference-root", str(reference), "--allow-mutating", "--require-all", "--continue-on-failure",
                           "--report", str(args.reports / "comparison.json")], prepare_case=prepare)
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
        for proxy in proxies:
            proxy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
