# Образ закреплён по дайджесту, Dependabot предлагает обновления.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# Образ от 16.11.2026 несёт zlib1g без исправления CVE-2026-85091, хотя Debian его уже выпустил в
# trixie-security. Обновляется только этот пакет. Шаг убирается, когда исправление войдёт в образ.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements/pip.lock requirements/runtime.lock requirements/build.lock ./requirements/
# Зависимости и сборочный бэкенд (setuptools) только из lock-файлов с хешами (SLSA Build, R2-SC-1);
# сам пакет собирается без изоляции, поэтому pip не скачивает незакреплённый setuptools.
RUN pip install --require-hashes -r requirements/pip.lock \
    && pip install --require-hashes -r requirements/runtime.lock -r requirements/build.lock
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src ./src
COPY examples ./examples
# pip и setuptools в образе после установки не нужны, а pip несёт вендоренные копии библиотек
# (urllib3, msgpack, pkg_resources из setuptools 70.3), которые Trivy справедливо считает уязвимыми.
# Колесо собирается локально, ставится как файл .whl без зависимостей: Scorecard Pinned-Dependencies
# считает такую установку закреплённой (у локального каталога «.» хеша нет).
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /opt/wheels . \
    && pip install --no-deps /opt/wheels/mandat-*.whl \
    && rm -rf /opt/wheels build src/*.egg-info \
    && python -m pip uninstall -y setuptools pip \
    && useradd --create-home --uid 10001 mandat && mkdir -p /app/var && chown mandat /app/var

USER mandat
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["mandat", "serve", "--host", "0.0.0.0", "--port", "8000"]
