# Образ закреплён по дайджесту, Dependabot предлагает обновления.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements/runtime.lock ./requirements/runtime.lock
# Зависимости только из lock-файла с хешами; сам пакет ставится без разрешения зависимостей.
RUN pip install --require-hashes -r requirements/runtime.lock
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src ./src
COPY examples ./examples
RUN pip install --no-deps . && useradd --create-home --uid 10001 mandat && mkdir -p /app/var && chown mandat /app/var

USER mandat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["mandat", "serve", "--host", "0.0.0.0", "--port", "8000"]
