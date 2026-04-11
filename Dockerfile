FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends darkplaces && \
    rm -rf /var/lib/apt/lists/* && \
    dpkg -L darkplaces | grep -E '/usr/(games|bin)/'

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["/usr/games/darkplaces-dedicated"]
CMD ["-basedir", "/var/games/quake", "-game", "airquake", "+exec", "server.cfg", "+map", "air1"]
