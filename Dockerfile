FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends darkplaces && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["/usr/games/darkplaces"]
CMD ["-dedicated", "-basedir", "/var/games/quake", "-game", "airquake", "+exec", "server.cfg", "+map", "air1"]
