FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FOODSAVER_DB_PATH=/data/foodsaver.db

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --system foodsaver \
    && useradd --system --gid foodsaver --home-dir /app foodsaver

COPY main.py ./main.py
COPY static ./static
COPY scripts ./scripts

RUN mkdir -p /data \
    && chown -R foodsaver:foodsaver /app /data

VOLUME ["/data"]
EXPOSE 8000
USER foodsaver

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

CMD ["python", "scripts/monitor_uvicorn.py", "--host", "0.0.0.0", "--port", "8000"]
