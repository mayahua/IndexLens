FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src/indexlens/ src/indexlens/
RUN python -m pip install -c requirements.lock setuptools wheel \
    && python -m pip install --no-build-isolation -c requirements.lock . \
    && python -m pip check \
    && useradd --uid 10001 --create-home indexlens \
    && mkdir -p /app/data \
    && chown indexlens:indexlens /app/data
COPY alembic.ini ./
COPY migrations/ migrations/
COPY scripts/ scripts/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "indexlens.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
