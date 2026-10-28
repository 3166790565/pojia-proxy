FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY presets ./presets
COPY web ./web

RUN useradd -r -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

USER appuser

ENV DB_PATH=/data/relay.db \
    PRESET_DIR=/data/presets \
    DATA_DIR=/data

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/admin/health', timeout=3)"

# SQLite 单写者模型：使用单 worker，避免多进程并发写库与重复初始化。
# 代理转发是异步 IO，单 worker 足以支撑常规并发。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
