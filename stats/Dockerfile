FROM python:3.12-slim

WORKDIR /app

COPY parse_stats.py .

EXPOSE 26100

ENTRYPOINT ["python", "-u", "parse_stats.py"]
# nqserver (the shared native+browser game server, see docker-compose.yml)
# writes each run's log to a freshly timestamped session directory rather
# than one stable path -- --log-glob re-resolves to the newest match on
# every refresh. No --fifo/welcome-message equivalent is wired up for
# nqserver yet (it has no shared-volume console FIFO the way
# quake-airquake did -- Nexus manages its process lifecycle instead), so
# that feature is dropped here rather than left silently broken.
CMD ["--log-glob", "/logs/*/server.log", "--serve", "--port", "26100", "--interval", "60", "--hostname", "DomWilkos Server"]
