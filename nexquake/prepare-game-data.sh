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

# Must match ../game/airquake/server.cfg's sv_votable_maps and
# nexquake/game/airquake/server/server.cfg's sv_votable_maps/mapcycle --
# all three are the set of maps actually confirmed to load under this
# engine and support the AirQuake total conversion (empirically verified by
# spinning up each map's .bsp and checking for load errors, not just
# trusting map readmes -- the rest of the source archive is either generic
# vanilla-Quake DM maps, or maps for "AirQuake2", an unrelated Quake II mod
# that happened to ship in the same archive despite AirQuake-sounding
# names/readmes -- those fail outright with a BSP-version error under this
# engine). Update all three if this list changes.
AIRQUAKE_MAPS="airdmd1 airdmd2 airfox airmine airw1 bjair1 belgrade canyon flight57 gb1 ground1 seastrik train1 train2"

# Config/rc files the dedicated server ALSO execs (Host_Init runs
# autoexec.cfg for -dedicated too, not just interactive clients), so they
# need to exist in both layers -- the browser client and the spawned
# server are separate engine processes, each only seeing common+client or
# common+server respectively.
CFG_FILES="AUTOEXEC.CFG QUAKE.RC AIRSAY.RC AQALIAS.CFG CONFIG.CFG MAP00.CFG MAP01.CFG MAP02.CFG MAP03.CFG"

rm -rf "$DST/id1" "$DST/airquake/common" "$DST/airquake/client" "$DST/airquake/server"
mkdir -p "$DST/id1/common" "$DST/airquake/common" "$DST/airquake/client" "$DST/airquake/server"

# Case-insensitive lookups throughout: this repo is developed on a
# case-insensitive filesystem (Windows) but deployed on Linux, and the
# actual on-disk casing of these files has already been observed to differ
# between the two (e.g. pak0.pak vs PAK0.PAK) -- never assume a fixed case.
# -L: game/id1 and game/airquake are themselves often symlinks (e.g. into
# /opt/airquake on a real deployment host) -- without it, find refuses to
# descend into a symlinked starting point at all on some find builds.
for name in PAK0.PAK PAK1.PAK; do
  match=$(find -L "$SRC/id1" -maxdepth 1 -iname "$name" | head -n1)
  if [ -n "$match" ]; then
    cp "$match" "$DST/id1/common/$(basename "$match")"
  else
    echo "prepare-game-data: warning: $name not found in $SRC/id1" >&2
  fi
done

rsync -a --exclude=console.fifo --exclude=qconsole.log --exclude=accounts.dat \
    "$SRC/airquake/" "$DST/airquake/common/"

for f in $CFG_FILES; do
  match=$(find "$DST/airquake/common" -maxdepth 1 -iname "$f" | head -n1)
  if [ -n "$match" ]; then
    base=$(basename "$match")
    cp "$match" "$DST/airquake/client/$base"
    mv "$match" "$DST/airquake/server/$base"
  fi
done
match=$(find "$DST/airquake/common" -maxdepth 1 -iname "server.cfg" | head -n1)
if [ -n "$match" ]; then
  mv "$match" "$DST/airquake/server/server.cfg"
else
  echo "prepare-game-data: warning: server.cfg not found in $SRC/airquake" >&2
fi

# quakespasm always looks for "maps/<name>.bsp" (see sv_main.c:
# q_snprintf(sv.modelname, ..., "maps/%s.bsp", server)) -- without an
# actual lowercase maps/ subdirectory, +map/vote requests fail with
# "Couldn't spawn server maps/<name>.bsp" and silently fall back to id1's
# stock map. Search recursively (not just top-level) since this archive's
# BSPs have been observed sitting loose at the top level on one copy of
# this data and properly inside a real MAPS/ subdirectory on another --
# don't assume either layout. The source archive is also known to carry a
# pre-existing "maps" -> "MAPS" symlink alias (from entrypoint.sh's
# case-folding workaround, already applied to some deployments) -- rsync
# would have copied that symlink verbatim, so start this directory fresh
# rather than inheriting it (copying a real file onto a path that's a
# symlink back to itself fails with "are the same file").
rm -rf "$DST/airquake/common/maps"
mkdir -p "$DST/airquake/common/maps"
for name in $AIRQUAKE_MAPS; do
  match=$(find "$DST/airquake/common" -iname "${name}.bsp" -type f | head -n1)
  if [ -n "$match" ]; then
    cp "$match" "$DST/airquake/common/maps/$(basename "$match")"
  else
    echo "prepare-game-data: warning: $name.bsp not found in $SRC/airquake" >&2
  fi
done

echo "Done. $DST/id1 and $DST/airquake are ready for the nexquake service."
