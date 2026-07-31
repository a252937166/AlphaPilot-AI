FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/run_financial_backfill.py ./scripts/run_financial_backfill.py
COPY scripts/export_financial_snapshot.py ./scripts/export_financial_snapshot.py

RUN python -m pip install --no-cache-dir . "baostock>=0.9.3,<1" \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin alphapilot

USER 10001:10001

CMD ["python", "-u", "scripts/run_financial_backfill.py"]
