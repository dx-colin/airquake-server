FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends darkplaces && \
    rm -rf /var/lib/apt/lists/* && \
    dpkg -L darkplaces | grep -E '/usr/(games|bin)/'

WORKDIR /var/games/quake

EXPOSE 26000/udp

ENTRYPOINT ["/bin/sh", "-c", "echo '=== Darkplaces binaries ===' && find /usr -name 'darkplaces*' -type f && echo '=== Done ===' && exit 1"]
