# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# --- development: hot reload, dev tools, source bind-mounted by compose ------
FROM base AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log"]

# --- production: no reload, no dev tools, no tests, non-root -----------------
FROM base AS production
RUN useradd --create-home --uid 10001 cermat \
 && mkdir -p /app/data/uploads \
 && chown -R cermat:cermat /app/data
COPY --chown=cermat:cermat alembic.ini ./
COPY --chown=cermat:cermat migrations ./migrations
COPY --chown=cermat:cermat app ./app
COPY --chown=cermat:cermat scripts ./scripts
COPY --chown=cermat:cermat docker-entrypoint.sh ./
USER cermat
ENV APP_ENV=production
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"
ENTRYPOINT ["sh", "./docker-entrypoint.sh"]
