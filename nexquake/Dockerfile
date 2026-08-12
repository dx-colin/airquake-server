# Browser-play front end for the AirQuake server, using NexQuake
# (https://github.com/0xBrsm/NexQuake) as a WebSocket<->UDP relay + WASM
# Quake client.
#
# Server binary: NOT our QuakeSpasm build (vendor/quakespasm, used by the
# main quake-airquake service). QuakeSpasm's wire format doesn't work with
# NexQuake's WASM client -- confirmed empirically: a connection through it
# hangs forever at signon, identically for AirQuake and for plain vanilla
# id1 content, while NexQuake's own bundled server connects instantly. So
# instead this builds NexQuake's own patched WinQuake-derived server from
# source (same as their own `nqserver`) via their build/prepare-upstream.sh
# (vendored at nexquake/vendor/NexQuake -- build/, bugfix/, server/ only;
# nexus/ and client/ come from their published image below, unmodified),
# then overlays our accounts/login/voting/admin patch
# (nexquake/server-patch/) on top -- see nexquake/README.md for the full
# story and sv_accounts.c for the patch's design notes. Same account store
# as quake-airquake either way (see docker-compose.yml).
#
# Build context must be the repo root (not this directory) -- see
# docker-compose.yml's `context: .` / `dockerfile: nexquake/Dockerfile`.

FROM debian:bookworm-slim AS server-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential git patch ca-certificates bash libcrypt-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY nexquake/vendor/NexQuake/build build/
COPY nexquake/vendor/NexQuake/bugfix bugfix/
COPY nexquake/vendor/NexQuake/server server/

# Fetches upstream WinQuake and applies NexQuake's own bugfix + server
# patches -- the exact same tree their bundled nqserver is built from.
# Invoked via `bash` rather than `./...` -- the executable bit doesn't
# reliably survive git archive -> scp -> COPY on every path this build gets
# run from (e.g. a Windows dev machine), so don't depend on it.
RUN OUT_DIR=build/tmp/server bash build/prepare-upstream.sh server

# Our patch overlays on top of their prepared tree: new sv_accounts.c/.h,
# and our modified server.h/host.c/host_cmd.c/pr_cmds.c/sv_main.c/
# sys_linux.c/Makefile.dedicated (full files, not diffs -- see
# nexquake/README.md for why, and the hook points touched in each).
COPY nexquake/server-patch/ build/tmp/server/

RUN cd build/tmp/server && make -j"$(nproc)" -f Makefile.dedicated
RUN cp "$(ls -1 /src/build/tmp/server/build-netquake-*/nqserver | head -n1)" /out-nqserver

# Pull Nexus's own prebuilt Go relay + WASM client + config defaults rather
# than rebuilding their Go/Emscripten toolchain ourselves -- Nexus is a
# static (CGO_ENABLED=0) binary with no runtime library needs of its own,
# and the WASM client is just static assets Nexus serves over HTTP, so
# neither cares what base image they end up running/served from.
FROM ghcr.io/0xbrsm/nexquake:latest AS nexquake-upstream

FROM debian:bookworm-slim

# libcrypt1 for our accounts patch's crypt_r() password hashing.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libcrypt1 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p bin/nqwasm server game logs etc

COPY --from=nexquake-upstream /app/bin/nexus ./bin/nexus
COPY --from=nexquake-upstream /app/bin/nqwasm ./bin/nqwasm
COPY --from=nexquake-upstream /app/etc ./etc
COPY --from=server-builder /out-nqserver ./server/nqserver

EXPOSE 1337
ENTRYPOINT ["/app/bin/nexus"]
