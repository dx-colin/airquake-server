FROM python:3.12-slim

WORKDIR /app

COPY quake_manage.py .

EXPOSE 26200

ENTRYPOINT ["python", "-u", "quake_manage.py"]
CMD ["--accounts-dir", "/data", "--server-cfg", "/data/server.cfg", "--port", "26200"]
