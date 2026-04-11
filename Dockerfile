FROM debian:bookworm-slim

RUN sed -i 's/Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends quake-server && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["quake-server"]
CMD ["-dedicated", "-game", "airquake", "+exec", "server.cfg", "+map", "air1"]
