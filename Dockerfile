# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run prod


FROM python:3.13-slim AS python-builder
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        cmake \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir poetry==2.4.1
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root


FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_ROOT=/app/static \
    MEDIA_ROOT=/app/media
WORKDIR /app
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgomp1 \
        libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app
COPY --from=python-builder /app/.venv /opt/venv
COPY --chown=app:app . .
COPY --from=frontend --chown=app:app /app/assets/css/styles.css /app/assets/css/styles.css
COPY --from=frontend --chown=app:app /app/node_modules/leaflet/dist /app/node_modules/leaflet/dist
RUN mkdir -p /app/media /app/static /app/chat-models \
    && python manage.py collectstatic --noinput \
    && chown -R app:app /app/media /app/static /app/chat-models
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8000), 3).close()"
CMD ["gunicorn", "fortissimusbellator.wsgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "gthread", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-"]
