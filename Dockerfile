FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y quake-server && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["quake-server"]
CMD ["-dedicated", "-game", "airquake", "+exec", "server.cfg", "+map", "air1"]
