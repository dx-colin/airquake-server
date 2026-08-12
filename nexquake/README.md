# Browser Play (NexQuake)

Lets you (and friends) play AirQuake in a browser tab, using
[NexQuake](https://github.com/0xBrsm/NexQuake) as a WASM Quake client + Go
relay that tunnels UDP over WebSocket.

**The server binary is NOT our QuakeSpasm build** (`../vendor/quakespasm`,
used by the main `quake-airquake` service). QuakeSpasm's wire format
doesn't work with NexQuake's browser client — confirmed empirically during
development: a connection through it hung forever at signon, identically
for AirQuake and for completely vanilla `id1` content, while NexQuake's own
bundled server connected instantly. So instead, `nexquake/Dockerfile`
builds NexQuake's own WinQuake-derived `nqserver` from source (the exact
same way their own image builds it — fetch upstream WinQuake, apply their
bugfix + server patches) and layers our accounts/login/voting/admin patch
(`nexquake/server-patch/`, a full-file overlay applied after their patches)
on top. It's the same design as the QuakeSpasm patch (see
`../vendor/quakespasm/Quake/sv_accounts.c`'s doc comment for the full
rationale), ported onto the older codebase — mechanical differences only
(no `q_strlcpy`/`q_snprintf` here, `Sys_FloatTime` instead of
`Sys_DoubleTime`, etc.), same command set, same account store format.

It shares the **same `accounts.dat`** as `quake-airquake` (see
`docker-compose.yml`'s `nexquake` volumes and `-accountsdir` in
`servers.ini`), so one account and one admin bootstrap
(`AIRQUAKE_ADMIN_USER`/`PASS` on the `quake-airquake` service) works from
both native client and browser.

## Setup

1. Run `./nexquake/prepare-game-data.sh` (needs `rsync`) to build
   `nexquake/game/` from your already-populated `../game/` directory.
   Idempotent — safe to re-run after updating PAK files or mod configs.
2. `docker compose up -d nexquake` (or just `docker compose up -d` for
   everything).
3. Open `http://<your-server-ip>:1337`.
4. In-game, press **`~`** (tilde/backtick) to open the real console (not
   chat/`say` — text typed there is public and won't reach `register`/
   `login`/etc). Browser backtick-key handling is occasionally flaky
   depending on browser/keyboard layout; click into the game window first
   if it doesn't respond.

## Important: keep this private

NexQuake's browser client downloads game files (including `PAK1.PAK`, the
retail data) from the server on demand to build its in-browser filesystem.
The upstream project's own README says explicitly: **"do not host PAK1.PAK
publicly."** Unlike a native Quake client (where players must already own
and supply their own copy of the game), anyone who loads this page gets
served the actual retail files. Don't set `EXTERNAL_URL` or otherwise
expose this to the open internet — keep it LAN-only, VPN-only, or behind an
access gate. This is why `docker-compose.yml` doesn't set `EXTERNAL_URL` —
that's deliberate, not an oversight.

## Things that had to be fixed to get here

NexQuake's process manager (Nexus) expects a different game-data layout
than our main setup — assets split into `common/` (shared), `client/`
(browser-only), and `server/` (dedicated-only) layers, merged into a
lowercased, symlinked overlay at runtime (see
`nexus/internal/assets/serverfs.go`'s `PrepareRuntimeBasedir` in the
NexQuake source). A few things fell out of getting a real match working,
in case any of it needs revisiting later:

- **QuakeSpasm doesn't work with this client at all.** See above — this is
  the reason the server here is a from-source WinQuake build, not our
  QuakeSpasm binary.
- **accounts.dat and Nexus's ephemeral runtime dir.** Nexus spawns each
  server inside a throwaway temp directory that gets deleted on every
  restart. Left alone, our engine would write `accounts.dat` in there and
  lose it on every restart — exactly the problem we built persistent
  accounts to avoid. Fixed with a new `-accountsdir <path>` engine flag
  (see `sv_accounts.c`'s `g_accounts_dir`) that overrides where
  `accounts.dat` lives, pointed at a real bind-mounted path instead.
- **`maps/` subdirectory.** This engine always looks for `maps/<name>.bsp`.
  AirQuake's archive has its BSPs sitting loose at the top level — the
  archive's own `MAPS/` folder is empty — which works for the main
  QuakeSpasm-based service today only because nobody had actually
  exercised the random-map-select path successfully (see the note left in
  `../entrypoint.sh` — this is a pre-existing gap there too, not something
  new to this integration). `prepare-game-data.sh` copies the whitelisted
  maps into an actual `common/maps/` directory to fix this.
- **Hostname/map defaults.** `servers.ini`'s `@def` line runs `+exec
  server.cfg` before `+hostname` (so our hostname wins over server.cfg's)
  and forces `+map airdmd1` explicitly (without it, the server falls back
  to id1's stock `start` map, which isn't built for the mod). Players can
  `/vote` a different map once connected.
- **Idle mapcycle landing on a non-AirQuake map.** NexQuake's own
  `host.c.patch` force-changes the map after `(timelimit + 1)` idle
  minutes with nobody connected. With their `mapcycle` cvar unset, that
  logic falls back to searching the *current* map for a
  `trigger_changelevel` entity and jumping to whatever it points at —
  which on `airdmd1` turned out to be a non-AirQuake map (surfaced as a
  burst of "No spawn function for ..." warnings after ~16 idle minutes).
  Fixed by setting `mapcycle` explicitly in `server.cfg` to the same
  whitelist as `sv_votable_maps`, so idle rotation always lands on a known
  AirQuake map.

## Layout

```
nexquake/
  Dockerfile               Multi-stage build: NexQuake's nqserver (from source,
                            our patch overlaid) + NexQuake's published nexus/wasm artifacts
  prepare-game-data.sh      Rebuilds game/ below from ../game/ (gitignored, proprietary data)
  server-patch/             Full-file overlay applied after NexQuake's own server patches:
                            sv_accounts.c/.h (new), server.h/host.c/host_cmd.c/pr_cmds.c/
                            sys_linux.c (modified), Makefile.dedicated (adds sv_accounts.o, -lcrypt)
  vendor/NexQuake/          Vendored build/, bugfix/, server/ from the NexQuake repo --
                            just enough to reproduce their nqserver build; nexus/ and the
                            WASM client come from their published image, unmodified
  game/
    servers.ini             Launch plan -- nqserver (ours, not NexQuake's default), tracked
    id1/common/              Base Quake PAKs (gitignored)
    airquake/
      common/                 Shared client+server assets incl. maps/ (gitignored)
      client/                 Browser-only configs (tracked, small text files)
      server/                 Dedicated-only configs incl. server.cfg (tracked, small text files)
```
