# two real servers, one disposable fixture

`python -m integration.live` boots the downloaded Zigcho release and the exact
bancho.py pin. PostgreSQL, MySQL, Redis and object storage belong to that one
GitHub runner. it refuses to run on the Mac or against production.

the complete corpus runs with `--require-all --continue-on-failure`. no transcript
is removed to get a green result. a case still stops at its first failed step;
the report must not describe those later steps as tested.
if both status replies prove the expected authenticated identity but their fields
differ, `--continue-on-failure` records that failed preflight and still exercises
the cases. the exit status remains failed, even if every case passes. missing,
wrong or unauthenticated identities still stop all mutations. no mismatch is waived.

the proxy only supplies each application's expected Host and synthetic location
headers. it does not change response bodies. the local mirror is upstream fixture
metadata, not an imitation of either server being compared.

fixture setup is separate from the transcripts. it drains old setup traffic at
case boundaries, records the actual packet ids, and creates the tournament room
only when that scenario needs it. this avoids an existing tournament room breaking
the multiplayer scenario's empty-lobby precondition. nothing drains between a
transcript's actions except its own declared requests.

the records use fake accounts, generated hardware hashes, one generated map per
set, and a generated replay. bot identity is recorded honestly: Kai is 3, the
pinned reference bot is 1. no response-wide normalisation hides the difference.
the friends response is parsed into individual ids, preserving order, duplicates
and its final newline. only the fixture bot id is mapped. the match-created
message likewise maps only its sender-id field after joining the action/drain
pair. a wrong id, changed message, extra packet or changed count still fails.

login has a declared bot-branding boundary too. the reference randomly changes
its bot status and sends deliberately off-map coordinates; Kai does neither.
the harness requires exactly one presence/stats packet for the fixed bot id,
zero bot gameplay values, and one bot friend entry. it records that the bot's
activity/presence branding is not compared. every human presence/stat, every
other friend id and all gameplay values remain checked. this is not a waiver for
missing bot packets, extra bot packets or a bot carrying scores.
the reference bootstrap runs its normal lifespan, then joins its permanent bot
to the same two public channels through `Player.join_channel`. that fixture
membership and the bootstrap file's hash are retained in the evidence. no
reference handler is replaced. medal image downloads are outside this corpus;
the reference's asset directory is prepared without downloading that catalogue.

## evidence limits

this is a new runner, not a completed parity claim. its first live run may expose
fixture mistakes as well as real compatibility differences. inspect each failure
before changing either a fixture or production behaviour. the fixture snapshot is
a seed recipe, not a post-run database-equivalence assertion.

even a clean run is synthetic HTTP evidence. installed Stable clients, real
redacted captures, all rulesets/RX/AP/ScoreV2 and tournament acceptance remain
separate work. this runner does not deploy or announce anything.
