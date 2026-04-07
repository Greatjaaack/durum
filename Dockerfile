FROM python:3.13-slim

ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/shifts.db \
    LOG_DIR=/app/logs

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir "poetry==${POETRY_VERSION}"

COPY pyproject.toml poetry.lock* /app/
RUN poetry install --no-ansi --only main --no-root

COPY app/ /app/app/
COPY README.md /app/README.md
RUN mkdir -p /app/logs /data

COPY camera_sync/ /app/camera_sync/
RUN pip install --no-cache-dir -r /app/camera_sync/requirements.txt

CMD ["python", "-m", "app.bot"]
