FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY workspace/ /app/
RUN chmod +x /app/kv.py /app/test_kv.py
