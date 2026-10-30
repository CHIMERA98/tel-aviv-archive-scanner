FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY scanner ./scanner
RUN pip install --upgrade pip && pip install . && useradd --create-home scanner

USER scanner
ENTRYPOINT ["python", "-m", "scanner.job"]
