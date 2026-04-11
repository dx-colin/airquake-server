# Airquake Server

A Dockerised Quake 1 dedicated server running the Airquake mod.

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
# Build the image
docker build -t quake-airquake .

# Run with Docker Compose
docker compose up -d
```

## Deploying via Portainer

1. Copy this repo to your Docker host (or point Portainer at the Git repo)
2. **Stacks → Add Stack → Repository** (or paste `docker-compose.yml` into the Web editor)
3. If using Web editor: build both images first on the host:
   ```bash
   docker build -t quake-airquake .
   docker build -t quake-stats ./stats
   ```
   Then replace the `build:` blocks with `image: quake-airquake` / `image: quake-stats`
4. Deploy the stack

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

The starting map is set in [Dockerfile](Dockerfile) (`+map air1`). To change it, edit the Dockerfile and rebuild:

```bash
docker build -t quake-airquake . && docker compose up -d
```

## Stats

A stats web UI is included as a second Docker service. It reads `qconsole.log` (written by the game server into `game/airquake/`) and serves a live stats page.

**Access:** http://&lt;your-server-ip&gt;:8080

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

## Logs

```bash
docker logs quake-airquake
```
