FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" >/etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY src /app/src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "-m", "tg_lottery.app"]

