# Builds QuakeSpasm from the vendored, patched source in vendor/quakespasm/
# (accounts/login, persistent per-account stats, map voting, admin commands
# -- see vendor/quakespasm/Quake/sv_accounts.c) instead of the stock
# Debian apt package. Multi-stage so the toolchain doesn't bloat the final
# image -- only the compiled binary and its runtime libs get copied over.

FROM debian:bookworm-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential libsdl2-dev libgl1-mesa-dev libcrypt-dev && \
    rm -rf /var/lib/apt/lists/*

COPY vendor/quakespasm /src
WORKDIR /src/Quake

# USE_SDL2=1 to match the runtime image's libsdl2 (stock QuakeSpasm defaults
# to SDL1.2). Codecs all disabled -- a dedicated server never plays audio,
# and each snd_*.c file compiles to an empty translation unit when its
# USE_CODEC_* flag is off (confirmed via snd_mp3.c etc.), so this needs no
# codec dev libraries at all.
RUN make -j"$(nproc)" USE_SDL2=1 \
        USE_CODEC_WAVE=0 USE_CODEC_FLAC=0 USE_CODEC_MP3=0 \
        USE_CODEC_VORBIS=0 USE_CODEC_OPUS=0 USE_CODEC_MIKMOD=0 \
        USE_CODEC_XMP=0 USE_CODEC_MODPLUG=0 USE_CODEC_UMX=0

FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libsdl2-2.0-0 libgl1 libcrypt1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /var/games/quake

COPY --from=builder /src/Quake/quakespasm /usr/games/quakespasm
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 26000/udp

ENTRYPOINT ["/entrypoint.sh"]
CMD ["-dedicated", "16", "-basedir", "/var/games/quake", "-game", "airquake", "-condebug", "+exec", "server.cfg"]
