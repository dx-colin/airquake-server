# Airquake Server

A Dockerised Quake 1 dedicated server running the Airquake mod, on a
patched QuakeSpasm engine (vendored at [vendor/quakespasm](vendor/quakespasm),
built from source rather than the stock package — see
[vendor/quakespasm/Quake/sv_accounts.c](vendor/quakespasm/Quake/sv_accounts.c)
for the patch itself). The patch adds player accounts, persistent
per-account stats, map voting, and an admin role — see
[Accounts, Admin & Voting](#accounts-admin--voting) below.

## Prerequisites

- Docker
- Quake 1 game files (requires owning the game — Steam/GOG both work)
- Airquake mod files (available from [Quaddicted](https://www.quaddicted.com))

## Directory Structure

Before building, place your game files here:

```
game/
├── id1/
│   ├── PAK0.PAK
│   └── PAK1.PAK
└── airquake/
    ├── PROGS.DAT
    ├── PAK0.PAK
    ├── PAK1.PAK
    ├── AUTOEXEC.CFG
    ├── QUAKE.RC
    ├── AIRSAY.RC
    ├── MAP00.CFG ... MAP0n.CFG
    └── *.BSP  (map files)
```

Copy from your GOG installation:
- `D:\Games\GOG Galaxy\Games\Quake\Id1\PAK0.PAK` → `game/id1/PAK0.PAK`
- `D:\Games\GOG Galaxy\Games\Quake\Id1\PAK1.PAK` → `game/id1/PAK1.PAK`
- Everything in `D:\Games\GOG Galaxy\Games\Quake\airquake\` → `game/airquake/`

The `game/` directory is gitignored — you need to populate it manually.

## Building & Running

```bash
# Build the image (compiles the patched QuakeSpasm engine from source --
# takes longer than a plain apt install, roughly a minute)
docker build -t quake-airquake .

# Run with Docker Compose
docker compose up -d
```

To create the first admin account, set `AIRQUAKE_ADMIN_USER` /
`AIRQUAKE_ADMIN_PASS` before starting (e.g. in a `.env` file next to
`docker-compose.yml`, or exported in your shell) — see
[Accounts, Admin & Voting](#accounts-admin--voting).

Iterating on the engine patch itself (`vendor/quakespasm/Quake/*.c`) is much
faster outside Docker: install `build-essential libsdl2-dev libgl1-mesa-dev
libcrypt-dev`, then `make -C vendor/quakespasm/Quake USE_SDL2=1
USE_CODEC_WAVE=0 USE_CODEC_FLAC=0 USE_CODEC_MP3=0 USE_CODEC_VORBIS=0
USE_CODEC_OPUS=0 USE_CODEC_MIKMOD=0 USE_CODEC_XMP=0 USE_CODEC_MODPLUG=0
USE_CODEC_UMX=0` — same flags the Dockerfile's builder stage uses, just
without the image-build round trip. Reserve the full `docker build` for
verifying the actual deployable image.

## Deploying via Portainer

**Option A — Git repo (Portainer builds the images):**

1. **Stacks → Add Stack → Repository**, point it at this repo
2. Compose path: `docker-compose.yml`
3. Deploy the stack

**Option B — Upload / Web editor from your PC (no repo access, so images must be prebuilt):**

1. Build both images on the Docker host:
   ```bash
   docker build -t quake-airquake .
   docker build -t quake-stats ./stats
   ```
2. **Stacks → Add Stack**, then either paste [docker-compose.portainer.yml](docker-compose.portainer.yml) into the Web editor, or use **Upload** and select that file
3. Deploy the stack

Both options bind-mount `/opt/airquake/id1` and `/opt/airquake/airquake` from the host — make sure your game files are there first (see [Directory Structure](#directory-structure) above, adjusted for the host path).

## Configuration

Server settings live in [game/airquake/server.cfg](game/airquake/server.cfg) — edit and restart the container, no rebuild needed:

| Setting       | Default           | Description                              |
|---------------|-------------------|------------------------------------------|
| `hostname`    | `My Airquake Server` | Server name shown in the browser      |
| `maxplayers`  | `16`              | Maximum concurrent players               |
| `fraglimit`   | `30`              | Frags to end the match                   |
| `timelimit`   | `15`              | Minutes per map                          |
| `deathmatch`  | `1`               | Game mode (`1` = DM)                     |
| `teamplay`    | `0`               | `0` = FFA, `1` = team damage on          |

The starting map is picked randomly at each container start by
[entrypoint.sh](entrypoint.sh), from a whitelist of maps confirmed to
actually support the AirQuake total conversion (`AIRQUAKE_MAPS` in that
script — the rest of the mod's `maps/` folder is generic vanilla-Quake DM
maps and one Quake II map that break spawning if loaded). That same list is
mirrored into `sv_votable_maps` in [server.cfg](game/airquake/server.cfg)
for `/vote` — update both if you add or remove maps.

## Accounts, Admin & Voting

Since AirQuake ships only a compiled `PROGS.DAT` (no mod source exists to
add real UI for this), all of this works through plain console commands —
type them at your own local Quake console (the `~` key), not in chat.
Nothing typed this way is ever shown to other players.

| Command | Who | What |
|---|---|---|
| `register <user> <pass>` | anyone | Create an account and log into it |
| `login <user> <pass>` | anyone | Log into an existing account |
| `logout` | anyone | Log out |
| `vote <mapname>` | anyone | Vote for the next map (see `sv_votable_maps` in server.cfg) — passes once `sv_vote_threshold` (default 50%) of connected players have voted for the same map |
| `admin <fraglimit\|timelimit\|hostname\|teamplay\|map\|changelevel\|kick> [args]` | admin accounts only | Change a server rule live, e.g. `admin fraglimit 40` |

**Prefix each of these with `cmd `** — e.g. `cmd register <user> <pass>`,
`cmd vote <mapname>` — not bare `register ...`/`vote ...`. Neither vkQuake
nor NexQuake's browser client auto-forwards a command it doesn't recognize
locally the way stock NetQuake historically did, so typing one bare just
prints "Unknown command" locally and never reaches the server at all. `cmd`
itself is a real, recognized client-side command in both engines, so
whatever follows it always gets forwarded regardless of whether the client
recognizes it. See [nexquake/README.md](nexquake/README.md)'s "Player
commands need the cmd prefix" section for how this was diagnosed.

Stats (kills/deaths/playtime) accrue automatically for whichever account
you're logged into, and persist in `accounts.dat` in the `airquake` game
directory — unlike the live stats page below, this survives container
restarts. Playing without logging in still works, it just isn't tracked.

**First admin account:** set `AIRQUAKE_ADMIN_USER` / `AIRQUAKE_ADMIN_PASS`
as environment variables on the `quake-airquake` service (see
[docker-compose.yml](docker-compose.yml)) and restart the container — this
creates the account if it doesn't exist, or resets its password/admin flag
if it does, so it also doubles as a password-reset path. Leave both unset
to skip bootstrapping.

## Stats

A stats web UI is included as a second Docker service. It reads `qconsole.log` (written by the game server into `game/airquake/`) and serves a live stats page.

**Access:** http://&lt;your-server-ip&gt;:26100

Stats tracked per player:
- Kills, deaths, suicides, K/D ratio, kill efficiency %
- Best kill streak, worst death streak
- Kills broken down by weapon
- Average kills per session, total sessions, total play time
- Favourite weapon and favourite map
- First seen / last seen timestamps

Also tracked globally:
- Match history (last 30 matches) with winner, scores, map, duration
- Map leaderboard: times played, total frags, average frags per play
- Server-wide totals: frags, matches, unique players

The stats page refreshes every 60 seconds automatically.

## Connecting

Default port: **26000 UDP** — make sure this is open on your firewall/router.

Connect in Quake with: `connect <your-server-ip>`

## Browser Play

You can also play in a browser tab, no Quake client install needed — see
[nexquake/README.md](nexquake/README.md). Same accounts/admin/voting as
above (they share the same account store). **Read the privacy note in that
README before exposing it beyond your LAN** — the browser client streams
game files, including the retail `PAK1.PAK`, to anyone who loads the page.

## Logs

```bash
docker logs quake-airquake
```
