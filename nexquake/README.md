# Browser Play (NexQuake)

Lets you (and friends) play AirQuake in a browser tab, using
[NexQuake](https://github.com/0xBrsm/NexQuake) as a WASM Quake client + Go
relay that tunnels UDP over WebSocket. `nqserver`, the dedicated server
this builds, is also the **single shared game server for native play
too** — it speaks plain NetQuake protocol 15, so a real client (vkQuake,
QuakeSpasm, ...) can `connect <host>:26001` directly, landing in the same
game as browser players. The old `quake-airquake` service (native-only,
`../vendor/quakespasm`) is retired from public play for exactly this
reason — it and `nqserver` used to be two separate, unlinked game worlds
that only shared an account database, which meant native and browser
players could never actually play together. `quake-airquake`'s image is
kept around only for its one-shot `-createadmin` admin bootstrap (see
`docker-compose.yml`'s comment on that service).

**The server binary is NOT our QuakeSpasm build** (`../vendor/quakespasm`,
used by the retired `quake-airquake` service). QuakeSpasm's wire format
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

It shares the **same `accounts.dat`** that `quake-airquake`'s one-shot
`-createadmin` bootstrap writes to (see `docker-compose.yml`'s `nexquake`
volumes and `-accountsdir` in `servers.ini`), so one admin bootstrap works
for both native and browser login.

## Native play

Connect directly to `nqserver`'s published port: `connect
airquake.eragon.io:26001` (or the LAN IP, e.g. `192.168.22.250:26001`, if
you're on the same home network as the host — see the NAT hairpin note
below). Requires port `26001/udp` forwarded on the router, same as `26000`
was for the old native-only setup.

If you're testing from the *same* home network the server runs on,
connecting via the public domain can hang at "Connection Accepted" and
never load in — that's NAT hairpin/loopback (common on consumer routers:
traffic has to loop back out through the router and back in, and a lot of
routers handle that badly for UDP). Confirmed via packet capture — the
symptom looks exactly like a broken signon, but it's the router. Use the
LAN IP directly when you're home; the public domain works fine from
anywhere else.

The domain root (`airquake.eragon.io`) is a small nickname-entry page
(`landing/index.html`, served by the `airquake-landing` service), not the
game itself. The game lives on its own subdomain,
`play.airquake.eragon.io` — see "Nickname entry" below for why and how
it's wired up.

## Setup

1. Run `./nexquake/prepare-game-data.sh` (needs `rsync`) to build
   `nexquake/game/` from your already-populated `../game/` directory.
   Idempotent — safe to re-run after updating PAK files or mod configs.
2. `docker compose up -d nexquake airquake-landing` (or just
   `docker compose up -d` for everything).
3. Open `http://<your-server-ip>/` (through Traefik) — or
   `http://<your-server-ip>:1337/` to hit the game directly, bypassing the
   landing page and Traefik.
4. In-game, press **`~`** (tilde/backtick) to open the real console (not
   chat/`say` — text typed there is public and won't reach `register`/
   `login`/etc). Browser backtick-key handling is occasionally flaky
   depending on browser/keyboard layout; click into the game window first
   if it doesn't respond.
5. **Player commands need the `cmd` prefix.** Type `cmd register <user>
   <pass>`, `cmd login <user> <pass>`, `cmd logout`, `cmd vote <map>`, or
   `cmd admin <subcommand> ...` — not bare `register ...`/`vote ...`/etc.
   Neither vkQuake nor NexQuake's browser client auto-forwards a command
   they don't recognize locally the way stock NetQuake historically did,
   so a bare custom command just prints "Unknown command" locally and
   never reaches the server at all — confirmed via a from-scratch,
   diagnostic-instrumented rebuild that logs every stringcmd the server
   actually receives: `name`/`color`/`prespawn`/`spawn`/`begin` (all
   things the client handles natively) show up every time, `register`
   never did, `cmd register ...` did. `cmd` itself IS a real client-side
   command in both engines (unlike the custom ones), so it always reaches
   the server regardless of whether the text after it is recognized.

## Nickname entry

NexQuake's client is the standard Quake UI (Setup menu / console `name`
command) — there's no web-native nickname box, and `CL_ARGS=+connect`
(auto-connect on load) means players never even see that menu. So instead:
`airquake-landing` serves a tiny static page (`landing/index.html`) at the
main domain root with a nickname field. Submitting it redirects to
`https://play.airquake.eragon.io/?+name&<nickname>`, which `nexquake`'s
`CL_URL_ARGS=1` turns into an extra `+name <nickname>` client startup arg
on top of `CL_ARGS`, so the player connects with that name already set.

`nexquake` is on its own subdomain rather than a subpath of the main
domain (e.g. `/play`) — a subpath was tried first and doesn't work: the
client's compiled JS calls several of its own API endpoints
(`/gamedir`, `/connect`, `/events`, `/rcon`, `/pak/`, `/nqseed/`, ...) via
hardcoded *absolute* root paths, not just relative asset references, so
none of them survive being proxied under a prefix. A separate subdomain
sidesteps this entirely — Nexus is genuinely at the root of its own
hostname, so every absolute path resolves correctly with no rewriting.
Costs one extra DNS record (`play.airquake.eragon.io`, added to the same
Cloudflare DDNS config as the main domain) and one extra Let's Encrypt
cert, both handled the same way as the main domain's.

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
- **Custom client commands silently dropped.** `register`/`login`/
  `logout`/`vote`/`admin` looked like they weren't reaching the server at
  all — no server-side trace whatsoever, not even a rejection, from either
  the browser client or a real native client (vkQuake), which ruled out a
  client-side quirk. Root cause: `sv_user.c` (upstream WinQuake/bugfix
  code, fetched fresh at build time — none of our other patched files
  touch client-command handling) has a hardcoded allowlist in
  `SV_ReadClientMessage`'s `clc_stringcmd` case gating which commands a
  non-privileged client may send to the server at all; anything not on it
  gets silently discarded before ever reaching `Cmd_ExecuteString`. Our
  five custom commands were never added to it. Fixed by adding `sv_user.c`
  to `server-patch/` (previously only referenced by the Makefile, not
  overlaid) with those five added to the allowlist.
- **accounts.dat and Nexus's ephemeral runtime dir.** Nexus spawns each
  server inside a throwaway temp directory that gets deleted on every
  restart. Left alone, our engine would write `accounts.dat` in there and
  lose it on every restart — exactly the problem we built persistent
  accounts to avoid. Fixed with a new `-accountsdir <path>` engine flag
  (see `sv_accounts.c`'s `g_accounts_dir`) that overrides where
  `accounts.dat` lives, pointed at a real bind-mounted path instead.
- **`maps/` subdirectory.** This engine always looks for `maps/<name>.bsp`.
  On the local Windows dev copy used while building this integration, the
  archive's own `MAPS/` folder was empty (BSPs sat loose at the top level)
  — `prepare-game-data.sh` copies the whitelisted maps into an actual
  `common/maps/` directory to handle that layout. This turned out to be
  specific to that one local copy, not a real production data issue — the
  actual production `/opt/airquake/airquake/MAPS/` archive was already
  properly populated, so the main QuakeSpasm-based service was never
  actually affected by this.
- **Hostname/map defaults.** `servers.ini`'s `@def` line runs `+exec
  server.cfg` before `+hostname` (so our hostname wins over server.cfg's)
  and forces `+map airw1` explicitly (without it, the server falls back to
  id1's stock `start` map, which isn't built for the mod). Players can
  `/vote` a different map once connected. `../entrypoint.sh` does the same
  for the native service (`DEFAULT_MAP`) — both are fixed to the same
  starting map, not randomized.
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
  landing/index.html        Nickname-entry page served at the domain root (airquake-landing
                            service) -- see "Nickname entry" above
  server-patch/             Full-file overlay applied after NexQuake's own server patches:
                            sv_accounts.c/.h (new), server.h/host.c/host_cmd.c/pr_cmds.c/
                            sv_main.c/sv_user.c/sys_linux.c (modified),
                            Makefile.dedicated (adds sv_accounts.o, -lcrypt)
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
