FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends quakespasm libsdl2-2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["/usr/games/quakespasm"]
CMD ["-dedicated", "-basedir", "/var/games/quake", "-game", "airquake", "+exec", "server.cfg", "+map", "airdmd1"]
