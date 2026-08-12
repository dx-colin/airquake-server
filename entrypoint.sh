#!/bin/sh
set -e

GAMEDIR=/var/games/quake/airquake

# quakespasm's -condebug always writes qconsole.log to the basedir root
# (/var/games/quake/), ignoring -game. That path isn't volume-mounted, so
# the log would be lost on restart and invisible to quake-stats. Symlink it
# into the airquake volume, which is mounted and shared with quake-stats.
ln -sf "$GAMEDIR/qconsole.log" /var/games/quake/qconsole.log

# The mod's files come from a decades-old DOS-era archive with inconsistent
# casing (MAPS/AIRDMD1.BSP, but also Canyon.txt, STunnel.txt, PROGS.DAT...).
# This quakespasm build's case-insensitive file lookup doesn't reliably fold
# case across a directory AND a filename together (e.g. "maps/airdmd1.bsp"
# resolving into "MAPS/AIRDMD1.BSP" silently fails -- no error, it just
# falls back to id1's start.bsp instead of the requested map). Create
# lowercase-named symlink aliases for everything so lookups always find a
# lowercase match. Idempotent -- skips anything with an existing lowercase
# counterpart, and find's default no-follow-symlinks behavior means the
# aliases created mid-walk don't get re-traversed.
find "$GAMEDIR" -mindepth 1 -depth 2>/dev/null | while IFS= read -r path; do
    dir=$(dirname "$path")
    base=$(basename "$path")
    lower=$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')
    if [ "$base" != "$lower" ] && [ ! -e "$dir/$lower" ]; then
        ln -s "$base" "$dir/$lower"
    fi
done

# AirQuake is a total conversion (players/monsters replaced by vehicles), so
# it requires maps built specifically for it -- see AIRQUAKE.TXT. This mod
# folder also has ~20 generic vanilla-Quake DM maps mixed into maps/ that
# happen to carry no vehicle spawn entities, plus airfox2c.bsp which is
# actually a Quake II AirQuake2 map. Loading any of those breaks spawning.
#
# DEFAULT_MAP must be one of the maps confirmed (via their .txt readmes) to
# require/support AirQuake TC for Quake 1, and must match one of
# game/airquake/server.cfg's sv_votable_maps entries, which the engine uses
# to validate /vote requests -- the two are kept in sync manually since one
# lives in shell and the other in a cvar the engine reads at runtime.
DEFAULT_MAP="airw1"

MAPS_DIR=$(find "$GAMEDIR" -maxdepth 1 -iname maps -type d | head -n1)
START_MAP=""
if [ -n "$MAPS_DIR" ]; then
    match=$(find "$MAPS_DIR" -maxdepth 1 -iname "${DEFAULT_MAP}.bsp" -size +0c | head -n1)
    [ -n "$match" ] && START_MAP="$DEFAULT_MAP"
fi

# This engine has no rcon (that's a QuakeWorld-only feature; vanilla NetQuake
# never implemented it -- confirmed absent from the binary). The only way to
# feed console commands (e.g. "say ...") to a running dedicated server is
# stdin at the local console. Wire that up to a FIFO on the shared volume so
# quake-stats can write commands into it cross-container. Open it read-write
# on our own fd first so the read-open below doesn't block waiting for a
# writer, and so quakespasm's stdin never sees EOF between messages.
CONSOLE_FIFO="$GAMEDIR/console.fifo"
[ -p "$CONSOLE_FIFO" ] || mkfifo "$CONSOLE_FIFO"
exec 3<>"$CONSOLE_FIFO"

# Admin account bootstrap/reset. -createadmin is idempotent (updates the
# password + admin flag if the account already exists), so running it on
# every start is harmless and doubles as a way to reset a forgotten admin
# password by just restarting the container with new env vars. It's a
# one-shot mode that loads/writes accounts.dat and exits before starting
# the actual game loop, so it's cheap even though it runs every start.
# Failure here (e.g. a too-short AIRQUAKE_ADMIN_PASS) must not take down
# the whole entrypoint under `set -e` -- log it and keep starting the game
# server, since a missing/broken admin bootstrap isn't fatal to play.
if [ -n "$AIRQUAKE_ADMIN_USER" ] && [ -n "$AIRQUAKE_ADMIN_PASS" ]; then
    echo "entrypoint: bootstrapping admin account '$AIRQUAKE_ADMIN_USER'"
    /usr/games/quakespasm -dedicated 16 -basedir /var/games/quake -game airquake \
        -createadmin "$AIRQUAKE_ADMIN_USER" "$AIRQUAKE_ADMIN_PASS" \
        || echo "entrypoint: admin bootstrap failed, continuing without it"
fi

if [ -n "$START_MAP" ]; then
    echo "entrypoint: starting on map '$START_MAP'"
    exec /usr/games/quakespasm "$@" +map "$START_MAP" 0<&3
else
    echo "entrypoint: default map '$DEFAULT_MAP' not found under $MAPS_DIR, falling back to configured default"
    exec /usr/games/quakespasm "$@" 0<&3
fi
