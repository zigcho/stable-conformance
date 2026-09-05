# mixed-load baseline

this is a workload, not a thousand installed osu clients and not a capacity certificate. it uses the harness's Stable packet contract, valid encrypted score submissions, real PostgreSQL and an isolated HTTPS object store. it only accepts a loopback HTTP origin and a loopback database named `zigcho_benchmark`, with an explicit mutation flag and fresh-fixture checks.

the default is 1,000 Stable osu!standard vanilla players, 10,000 accounts, 100,000 historical scores, 200 synthetic 600-object maps and 30 days of profile history. login and the score/retry preflight happen before measurement. the baseline keeps the server's pool at eight connections.

the login ramp keeps already-connected players polling on staggered schedules. it has a ten-minute safety limit, records its own timings and top database queries, and saves an incomplete report if setup fails. reaching fewer than the requested players is not a successful smaller benchmark.

the mix includes five spectator hosts and fifteen watchers, four four-player multiplayer rooms exchanging score frames, normal five-second polls, fifty chat senders, 60% of players submitting one score per 180 seconds, twenty website reads per second and replay reads. the schedule is seeded and bounded. slow actors report missed slots instead of silently pretending a lower offered rate was the target. the generator caps concurrent requests and each response body.

client percentiles count successful protocol responses, with a one-millisecond upper-bound resolution through ten seconds. errors, overflow, missed slots and scheduler lateness are reported separately. a quick error response cannot make the successful p99 look better. score responses must contain a receipt; HTTP 200 with `error: no` is a failure.

server durations use differences between the starting and ending histogram scrapes. reports also include RSS/CPU samples, pending work, PostgreSQL statement statistics, the exact running executable hash and commit, database size, persisted replay counts and received chat/spectator/multiplayer packets.

the cold-gameplay pass follows a server/database restart and cache reset in the disposable runner. logging players in necessarily warms account state. the next pass uses the same process and sessions, with the plays added by the first pass. this is not a claim that every layer is cold, nor an identical-data before/after optimization comparison.

the origin is direct HTTP; it excludes Cloudflare and Layerline. the HTTPS MinIO service is local, so its timings do not stand in for Singapore storage latency. the private anticheat module is absent; Stable replay preparation and built-in observation still run. lazer traffic, Relax/AP, a one-hour peak test and a longer soak need separate runs before claiming coverage for them.

use the server repo's hosted performance workflow. do not point this at production or turn off authorization to make a benchmark pass. normal harness tests do not need the optional `requirements.txt`; the performance runner installs its hash-pinned cipher dependency.
