"""Fixture database access. Deliberately refuses production names and remote hosts."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from urllib.parse import urlsplit

from benchmarks.fixtures import MAP_BASE, USER_BASE, beatmap


class Database:
    def __init__(self):
        self.dsn = os.environ.get("ZIGCHO_BENCHMARK_POSTGRES_URL", "")
        url = urlsplit(self.dsn)
        if url.scheme != "postgresql" or url.hostname != "127.0.0.1" or url.path != "/zigcho_benchmark" or url.query or url.fragment:
            raise ValueError("benchmark database must be loopback PostgreSQL named zigcho_benchmark")

    def sql(self, query: str, *, timeout: int = 120) -> str:
        result = subprocess.run(
            ["psql", self.dsn, "--no-psqlrc", "-qAt", "-v", "ON_ERROR_STOP=1"],
            input=query, text=True, capture_output=True, timeout=timeout,
        )
        if result.returncode:
            # Never echo DSNs, SQL values or credential-bearing stderr into reports.
            raise RuntimeError("isolated PostgreSQL fixture operation failed")
        return result.stdout.strip()

    def json(self, query: str):
        return json.loads(self.sql(query))

    def seed(self, accounts: int, scores: int, maps: int, active: int) -> dict:
        if not active <= accounts <= 100000 or not 1 <= scores <= min(1000000, accounts * maps) or not 20 <= maps <= 1000:
            raise ValueError("fixture size bounds exceeded")
        untouched = self.sql("SELECT (SELECT count(*) FROM zigcho.users WHERE id>3)=1 AND EXISTS(SELECT 1 FROM zigcho.users WHERE name='bench_seed') AND NOT EXISTS(SELECT 1 FROM zigcho.scores) AND NOT EXISTS(SELECT 1 FROM zigcho.beatmaps);")
        if untouched != "t":
            raise RuntimeError("refusing to seed anything except a fresh isolated fixture")
        self.sql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements; CREATE TABLE public.zigcho_benchmark_marker (name text PRIMARY KEY); INSERT INTO public.zigcho_benchmark_marker VALUES ('isolated-fixture-v1');")
        self.sql(f"""
            INSERT INTO zigcho.users(id,name,safe_name,email,password_hash,password_salt,country)
            SELECT {USER_BASE}+n,'bench_'||n,'bench_'||n,'bench_'||n||'@example.invalid',u.password_hash,u.password_salt,'AU'
            FROM generate_series(0,{accounts - 1}) n CROSS JOIN zigcho.users u WHERE u.name='bench_seed';
            INSERT INTO zigcho.stats(user_id,mode)
            SELECT {USER_BASE}+n,m FROM generate_series(0,{accounts - 1}) n CROSS JOIN unnest(ARRAY[0,1,2,3,4,5,6,8]) m;
        """)
        output = io.StringIO()
        csv_writer = csv.writer(output, lineterminator="\n")
        for index in range(maps):
            fixture = beatmap(index)
            csv_writer.writerow([fixture.id, fixture.id, fixture.md5, "zigcho benchmark", f"isolated workload {index}", "synthetic", "bench_0", 3, True, fixture.duration_ms // 1000, fixture.objects, 0, 150, 4, 8, 6, 5, "\\x" + fixture.data.hex(), fixture.objects, None, fixture.duration_ms // 1000])
        self.sql("COPY zigcho.beatmaps(id,set_id,md5,artist,title,version,creator,status,status_frozen,total_length,max_combo,mode,bpm,cs,ar,od,hp,osu_file,count_circles,creator_id,hit_length) FROM STDIN WITH CSV;\n" + output.getvalue() + "\\.\n")
        self.sql(f"""
            INSERT INTO zigcho.scores(user_id,map_md5,mode,mods,score,pp,accuracy,max_combo,n300,n100,n50,nmiss,ngeki,nkatu,perfect,passed,checksum,rank_namespace,best,time_elapsed,submitted_at)
            SELECT {USER_BASE}+n%{accounts},b.md5,0,0,1000000+n,20+n%200,1,600,600,0,0,0,0,0,true,true,md5('historical-'||n),'vanilla',true,121000,extract(epoch FROM clock_timestamp())::bigint-(n%720)*3600
            FROM generate_series(0,{scores - 1}) n JOIN zigcho.beatmaps b ON b.id={MAP_BASE}+(n/{accounts})%{maps};
            UPDATE zigcho.stats st SET total_score=q.total,ranked_score=q.total,plays=q.plays,play_time=q.plays*121,total_hits=q.plays*600,accuracy=1,max_combo=600,pp=q.pp
            FROM (SELECT user_id,sum(score) total,count(*)::integer plays,sum(pp)::integer pp FROM zigcho.scores GROUP BY user_id) q
            WHERE st.user_id=q.user_id AND st.mode=0;
            INSERT INTO zigcho.user_stats_history(user_id,source,mode,day,pp,global_rank)
            SELECT {USER_BASE}+n,'all',0,(extract(epoch FROM clock_timestamp())::bigint/86400-d)*86400,200+d,n+1
            FROM generate_series(0,{active - 1}) n CROSS JOIN generate_series(1,30) d;
            ANALYZE zigcho.users; ANALYZE zigcho.stats; ANALYZE zigcho.scores; ANALYZE zigcho.beatmaps; ANALYZE zigcho.user_stats_history;
        """, timeout=300)
        return self.size()

    def size(self) -> dict:
        return self.json("SELECT json_build_object('accounts',(SELECT count(*) FROM zigcho.users),'scores',(SELECT count(*) FROM zigcho.scores),'maps',(SELECT count(*) FROM zigcho.beatmaps),'history_rows',(SELECT count(*) FROM zigcho.user_stats_history),'database_bytes',pg_database_size(current_database()),'postgres_version',version());")

    def reset_statements(self) -> None:
        self.sql("SELECT pg_stat_statements_reset();")

    def statements(self) -> list:
        return self.json("SELECT coalesce(json_agg(q),'[]'::json) FROM (SELECT queryid::text,calls,total_exec_time,mean_exec_time,max_exec_time,rows,shared_blks_hit,shared_blks_read,temp_blks_written,left(query,2000) query FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND query NOT ILIKE '%pg_stat_statements%' ORDER BY total_exec_time DESC LIMIT 20) q;")

    def verify_scores(self, checksums: list[str]) -> dict:
        if len(checksums) > 20000 or any(len(value) != 32 or any(c not in "0123456789abcdef" for c in value) for value in checksums):
            raise ValueError("invalid bounded checksum list")
        if not checksums:
            return {"acknowledged": 0, "stored": 0, "with_replay": 0, "archived": 0, "duplicate_best_scopes": 0}
        literal = ",".join("'" + value + "'" for value in checksums)
        return self.json(f"SELECT json_build_object('acknowledged',{len(checksums)},'stored',(SELECT count(*) FROM zigcho.scores WHERE checksum IN ({literal})),'with_replay',(SELECT count(*) FROM zigcho.scores WHERE checksum IN ({literal}) AND octet_length(replay)>0),'archived',(SELECT count(*) FROM zigcho.replay_objects WHERE source='stable' AND score_id IN (SELECT id FROM zigcho.scores WHERE checksum IN ({literal}))),'duplicate_best_scopes',(SELECT count(*) FROM (SELECT user_id,map_md5,mode,rank_namespace FROM zigcho.scores WHERE best GROUP BY user_id,map_md5,mode,rank_namespace HAVING count(*)>1) q));")
