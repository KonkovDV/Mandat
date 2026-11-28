#!/usr/bin/env bash
# Стенд «Мандата» на чистом сервере Ubuntu 22.04 или 24.04: Python из пакетов системы, служба systemd,
# nginx с HTTPS (Let's Encrypt), HSTS, лимиты частоты, запрет индексации. Консоль сотрудника — /console/.
# Запуск от root из папки проекта (клон репозитория):
#   bash scripts/deploy_stand.sh stand.example.ru
# Модель-советник через OpenRouter: ключ передаётся переменной окружения запуска и попадает только в
# /etc/mandat-stand.env (права 600), в репозиторий и журнал выкладки — никогда:
#   MANDAT_STAND_LLM_KEY=sk-or-... bash scripts/deploy_stand.sh stand.example.ru
# MANDAT_STAND_LLM_KEY=off снимает модель. Повторный запуск обновляет код и перезапускает службу; настройки в
# /etc/mandat-stand.env сохраняются. Откат на предыдущую выкладку: bash scripts/deploy_stand.sh rollback
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/mandat-stand
STATE=/var/lib/mandat-stand
ENV_FILE=/etc/mandat-stand.env
UNIT=/etc/systemd/system/mandat-stand.service
SITE=/etc/nginx/sites-available/mandat-stand
PORT=${MANDAT_STAND_PORT:-8000}
LLM_KEY=${MANDAT_STAND_LLM_KEY:-}
LLM_MODEL=${MANDAT_STAND_LLM_MODEL:-mistralai/mistral-small-3.2-24b-instruct}
EMAIL=${MANDAT_STAND_EMAIL:-}
ACTION=deploy
if [ "${1:-}" = "rollback" ]; then ACTION=rollback; shift; fi
DOMAIN=${1:-${MANDAT_STAND_DOMAIN:-}}

log() { printf '\n== %s\n' "$*"; }
die() { printf 'ошибка: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "запустите от root"

python_bin() {
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      echo "$candidate"; return 0
    fi
  done
  return 1
}

install_package() {
  # Зависимости только из lock-файлов с хешами, как в Docker-образе; сам пакет — без зависимостей.
  local py
  py=$(python_bin)
  [ -x "$APP/.venv/bin/python" ] || "$py" -m venv "$APP/.venv"
  "$APP/.venv/bin/pip" install -q --require-hashes -r "$APP/requirements/pip.lock"
  "$APP/.venv/bin/pip" install -q --require-hashes -r "$APP/requirements/runtime.lock" -r "$APP/requirements/build.lock"
  "$APP/.venv/bin/pip" install -q --no-deps --no-build-isolation --force-reinstall "$APP"
}

self_check() {
  log "Проверка"
  local url="http://127.0.0.1:$PORT" jar sid
  for _ in $(seq 1 30); do curl -fsS "$url/health" >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS "$url/health" | grep -q '"console":true' || die "служба не отвечает или консоль выключена: journalctl -u mandat-stand -n 50"
  curl -fsS -o /dev/null "$url/console/" || die "страница консоли не открылась"
  # Сессия консоли и демо-кейс через HTTP API: cookie передаётся явно, у неё Secure для HTTPS.
  sid=$(curl -fsS -D - -o /dev/null -H 'Content-Type: application/json' -d '{}' "$url/console/api/session" \
    | tr -d '\r' | sed -n 's/^[Ss]et-[Cc]ookie: mandat_console=\([^;]*\).*/\1/p')
  [ -n "$sid" ] || die "сессия консоли не открылась"
  jar="mandat_console=$sid"
  curl -fsS -b "$jar" -H 'Content-Type: application/json' -d '{"demo":"14_old_director_charter_cleared"}' \
    "$url/console/api/check" | grep -q '"verdict":"confirmed"' || die "демо-кейс не прошёл проверку"
  curl -fsS -b "$jar" -H 'Content-Type: application/json' -d '{}' "$url/console/api/dbo/issue" \
    | grep -q '"verdict":"confirmed"' || die "удостоверение ДБО не выпущено"
  curl -fsS -b "$jar" -H 'Content-Type: application/json' -d '{"scenario":"pay"}' "$url/console/api/dbo/authorize" \
    | grep -q '"allowed":true' || die "допуск платежа не прошёл"
  [ "$(curl -sS -o /dev/null -w '%{http_code}' -I "$url/console/api/check")" = 405 ] \
    || die "HEAD на маршруте с действием не отвергнут"
  echo "служба: $(curl -fsS "$url/health")"
}

if [ "$ACTION" = rollback ]; then
  log "Откат на предыдущую выкладку"
  [ -d "$APP/src.prev" ] || die "предыдущей выкладки нет: $APP/src.prev"
  mv "$APP/src" "$APP/src.failed" && mv "$APP/src.prev" "$APP/src" && mv "$APP/src.failed" "$APP/src.prev"
  if [ -f "$APP/BUILD.prev" ]; then
    mv "$APP/BUILD" "$APP/BUILD.failed" 2>/dev/null || true
    mv "$APP/BUILD.prev" "$APP/BUILD"
    [ -f "$APP/BUILD.failed" ] && mv "$APP/BUILD.failed" "$APP/BUILD.prev"
  fi
  install_package
  chown -R root:root "$APP"
  systemctl restart mandat-stand
  self_check
  exit 0
fi

[ -n "$DOMAIN" ] || die "укажите домен: bash scripts/deploy_stand.sh stand.example.ru"

log "Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx curl ca-certificates >/dev/null
if ! python_bin >/dev/null; then
  # Ubuntu 22.04: системный Python 3.10, пакету нужен 3.11+ (universe).
  apt-get install -y -qq python3.11 python3.11-venv >/dev/null || die "нужен Python 3.11+: apt-get install python3.11 python3.11-venv"
fi
PYV=$(python_bin) || die "нужен Python 3.11+"
apt-get install -y -qq "$("$PYV" -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}-venv")')" >/dev/null \
  || apt-get install -y -qq python3-venv >/dev/null

log "Код в $APP"
id mandat >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin mandat
mkdir -p "$APP" "$STATE"
if [ "$SRC" != "$APP" ]; then
  rm -rf "$APP/src.new"
  cp -a "$SRC/src" "$APP/src.new"
  find "$APP/src.new" -name __pycache__ -prune -exec rm -rf {} +
  cp "$SRC/pyproject.toml" "$SRC/README.md" "$SRC/LICENSE" "$SRC/NOTICE.md" "$APP/"
  rm -rf "$APP/requirements" && cp -a "$SRC/requirements" "$APP/requirements"
  if [ -d "$APP/src" ]; then rm -rf "$APP/src.prev" && mv "$APP/src" "$APP/src.prev"; fi
  [ -f "$APP/BUILD" ] && cp "$APP/BUILD" "$APP/BUILD.prev"
  mv "$APP/src.new" "$APP/src"
fi
COMMIT=${MANDAT_COMMIT:-$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)}
if [ -n "$COMMIT" ]; then echo "$COMMIT" > "$APP/BUILD"; fi  # виден в /health: какой коммит работает на стенде
install_package
chown -R root:root "$APP"
chown mandat:mandat "$STATE" && chmod 700 "$STATE"

log "Настройки службы: $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<ENV
# Стенд «Мандата»: консоль сотрудника /console/ на вымышленных данных. В pilot и prod консоль не стартует.
MANDAT_ENV=demo
MANDAT_STAND=1
# Учётные записи консоли сервер создаёт сам на каждую сессию браузера: operator, reviewer (юрист), dbo.
# Токены живут в памяти службы и в браузер не уходят; admin и удаление кейсов консоли недоступны.
MANDAT_STAND_SECURE_COOKIE=1
MANDAT_STAND_DIR=$STATE
MANDAT_API_JOURNAL=$STATE/journal.jsonl
# За nginx адрес клиента для службы — 127.0.0.1: лимит по адресу держит nginx (limit_req).
MANDAT_API_RATE_PER_MIN=0
MANDAT_API_MAX_CONCURRENCY=64
ENV
fi
if [ -n "$LLM_KEY" ]; then  # строки MANDAT_LLM_* заменяются целиком
  sed -i '/^MANDAT_LLM_/d' "$ENV_FILE"
  if [ "$LLM_KEY" != off ]; then
    {
      echo "MANDAT_LLM_BASE_URL=https://openrouter.ai/api/v1"
      echo "MANDAT_LLM_MODEL=$LLM_MODEL"
      echo "MANDAT_LLM_API_KEY=$LLM_KEY"
      echo "MANDAT_LLM_ALLOWED_HOSTS=openrouter.ai"
    } >> "$ENV_FILE"
  fi
fi
chown root:root "$ENV_FILE" && chmod 600 "$ENV_FILE"

cat > "$UNIT" <<UNIT
[Unit]
Description=Mandat demo stand (staff console)
After=network-online.target
Wants=network-online.target

[Service]
User=mandat
Group=mandat
WorkingDirectory=$APP
EnvironmentFile=$ENV_FILE
ExecStart=$APP/.venv/bin/mandat serve --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$STATE
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q mandat-stand
systemctl restart mandat-stand

log "nginx и HTTPS для $DOMAIN"
cat > /etc/nginx/conf.d/mandat-stand-limits.conf <<'NGINX'
# Лимиты частоты по адресу клиента: API, действия консоли и тяжёлые действия (модель, прогон, шлюз).
limit_req_zone $binary_remote_addr zone=mandat_api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=mandat_heavy:10m rate=6r/m;
limit_req_status 429;
server_tokens off;
NGINX
REJECT_TLS=""
NGINX_VERSION=$(nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ "$(printf '%s\n1.19.4\n' "$NGINX_VERSION" | sort -V | head -1)" = 1.19.4 ]; then
  REJECT_TLS="    listen 443 ssl default_server;
    ssl_reject_handshake on;"
fi
cat > "$SITE" <<NGINX
# Чужое имя хоста — соединение закрывается без ответа (444), по HTTPS — без рукопожатия.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
$REJECT_TLS
    server_name _;
    return 444;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 1m;
    proxy_hide_header Strict-Transport-Security;
    proxy_hide_header X-Robots-Tag;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Robots-Tag "noindex, nofollow" always;
    proxy_set_header Host \$host;
    proxy_http_version 1.1;
    proxy_read_timeout 60s;

    location /v1/ {
        limit_req zone=mandat_api burst=20 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
    }
    location /access/v1/ {
        limit_req zone=mandat_api burst=20 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
    }
    location ~ ^/console/api/(eval|consult|gate)\$ {
        limit_req zone=mandat_heavy burst=6 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
    }
    location /console/api/ {
        limit_req zone=mandat_api burst=40 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
    }
    location / {
        limit_req zone=mandat_api burst=60 nodelay;
        proxy_pass http://127.0.0.1:$PORT;
    }
}
NGINX
ln -sf "$SITE" /etc/nginx/sites-enabled/mandat-stand
rm -f /etc/nginx/sites-enabled/default
if [ -n "$REJECT_TLS" ] && ! nginx -t -q 2>/dev/null; then
  # Старый nginx без ssl_reject_handshake: чужие имена отсекаются только по HTTP.
  sed -i '/listen 443 ssl default_server;/d;/ssl_reject_handshake on;/d' "$SITE"
fi
nginx -t -q && systemctl reload nginx
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow OpenSSH >/dev/null; ufw allow 'Nginx Full' >/dev/null; fi
CONTACT=(--register-unsafely-without-email)
[ -n "$EMAIL" ] && CONTACT=(-m "$EMAIL")
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos "${CONTACT[@]}" --redirect --keep-until-expiring -q; then
  URL="https://$DOMAIN/console/"
else
  URL="http://$DOMAIN/console/"
  echo "сертификат не выпущен (DNS домена должен указывать на этот сервер): стенд работает по HTTP"
  sed -i 's/^MANDAT_STAND_SECURE_COOKIE=1/MANDAT_STAND_SECURE_COOKIE=0/' "$ENV_FILE"
  systemctl restart mandat-stand
fi
if [ "${MANDAT_STAND_FAIL2BAN:-0}" = 1 ]; then
  # По желанию: бан адресов, которые упираются в limit_req. Жюри за одним NAT лучше не банить — по умолчанию выключено.
  apt-get install -y -qq fail2ban >/dev/null
  cat > /etc/fail2ban/jail.d/mandat-stand.conf <<'JAIL'
[nginx-limit-req]
enabled = true
port = http,https
logpath = /var/log/nginx/error.log
findtime = 60
maxretry = 30
bantime = 600
JAIL
  systemctl restart fail2ban
fi

self_check
if [ "${URL#https}" != "$URL" ]; then
  curl -fsSI "$URL" | grep -qi '^strict-transport-security' || die "HTTPS без HSTS: проверьте nginx"
fi
echo
echo "Стенд:   $URL"
echo "Здоровье: ${URL%/console/}/health"
echo "Журнал службы: journalctl -u mandat-stand -f"
