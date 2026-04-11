FROM python:3.12-slim

WORKDIR /app

COPY parse_stats.py .

EXPOSE 8080

ENTRYPOINT ["python", "parse_stats.py"]
CMD ["--log", "/data/qconsole.log", "--serve", "--port", "8080", "--interval", "60"]
