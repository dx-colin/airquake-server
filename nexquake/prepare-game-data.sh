#!/bin/bash
# Builds nexquake/game/ from the existing game/ directory, reorganised into
# NexQuake's common/client/server layout (see docs/SETUP.md in the
# NexQuake project). Safe to re-run any time game/ changes (e.g. after
# updating PAK files or AUTOEXEC.CFG) -- it always rebuilds from scratch.
#
# Requires rsync. Run from the repo root: ./nexquake/prepare-game-data.sh
set -e
cd "$(dirname "$0")/.."

SRC=game
DST=nexquake/game

# Must match entrypoint.sh's AIRQUAKE_MAPS -- see that file's comment for
# why only these maps are safe to load (the rest of the archive is generic
# vanilla-Quake DM maps and one Quake II map that break AirQuake spawning).
AIRQUAKE_MAPS="airdmd1 airdmd2 airfox airmine airw1 bjair1"

# Config/rc files the dedicated server ALSO execs (Host_Init runs
# autoexec.cfg for -dedicated too, not just interactive clients), so they
# need to exist in both layers -- the browser client and the spawned
# server are separate engine processes, each only seeing common+client or
# common+server respectively.
CFG_FILES="AUTOEXEC.CFG QUAKE.RC AIRSAY.RC AQALIAS.CFG CONFIG.CFG MAP00.CFG MAP01.CFG MAP02.CFG MAP03.CFG"

rm -rf "$DST/id1" "$DST/airquake/common" "$DST/airquake/client" "$DST/airquake/server"
mkdir -p "$DST/id1/common" "$DST/airquake/common" "$DST/airquake/client" "$DST/airquake/server"

cp "$SRC/id1/PAK0.PAK" "$SRC/id1/PAK1.PAK" "$DST/id1/common/"

rsync -a --exclude=console.fifo --exclude=qconsole.log --exclude=accounts.dat \
    "$SRC/airquake/" "$DST/airquake/common/"

for f in $CFG_FILES; do
  if [ -f "$DST/airquake/common/$f" ]; then
    cp "$DST/airquake/common/$f" "$DST/airquake/client/$f"
    mv "$DST/airquake/common/$f" "$DST/airquake/server/$f"
  fi
done
mv "$DST/airquake/common/server.cfg" "$DST/airquake/server/server.cfg"

# The archive's actual BSPs sit loose at the top level (its MAPS/
# subdirectory is empty) but quakespasm always looks for "maps/<name>.bsp"
# (see sv_main.c: q_snprintf(sv.modelname, ..., "maps/%s.bsp", server)) --
# without this, +map/vote requests for these maps fail with "Couldn't spawn
# server maps/<name>.bsp" and silently fall back to id1's stock map.
mkdir -p "$DST/airquake/common/maps"
for name in $AIRQUAKE_MAPS; do
  match=$(find "$DST/airquake/common" -maxdepth 1 -iname "${name}.bsp" | head -n1)
  if [ -n "$match" ]; then
    cp "$match" "$DST/airquake/common/maps/$(basename "$match")"
  else
    echo "prepare-game-data: warning: $name.bsp not found in $SRC/airquake" >&2
  fi
done
rmdir "$DST/airquake/common/MAPS" 2>/dev/null || true

echo "Done. $DST/id1 and $DST/airquake are ready for the nexquake service."
