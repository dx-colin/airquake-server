FROM python:3.12-slim

WORKDIR /app

COPY parse_stats.py .

EXPOSE 26100

ENTRYPOINT ["python", "parse_stats.py"]
CMD ["--log", "/data/qconsole.log", "--serve", "--port", "26100", "--interval", "60"]
