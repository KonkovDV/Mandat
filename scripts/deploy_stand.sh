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
# Эксплуатация: часовой пояс Москвы, swap на машине меньше 2 ГБ, потолок журнала systemd, сторож здоровья службы,
# ежедневная копия данных в 03:15 МСК (14 копий), fail2ban для sshd и nginx, автоматические обновления безопасности.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/mandat-stand
STATE=/var/lib/mandat-stand
BACKUPS=/var/backups/mandat-stand
ENV_FILE=/etc/mandat-stand.env
UNIT=/etc/systemd/system/mandat-stand.service
SITE=/etc/nginx/sites-available/mandat-stand
WATCHDOG=/usr/local/sbin/mandat-stand-watchdog
PORT=${MANDAT_STAND_PORT:-8000}
LLM_KEY=${MANDAT_STAND_LLM_KEY:-}
LLM_MODEL=${MANDAT_STAND_LLM_MODEL:-mistralai/mistral-small-3.2-24b-instruct}
LLM_BUDGET=${MANDAT_STAND_LLM_BUDGET:-1}
# OpenRouter: только провайдеры без хранения запросов и стоимость вызова в ответе (её считает дневной бюджет).
LLM_EXTRA='{"provider":{"data_collection":"deny"},"usage":{"include":true}}'
EMAIL=${MANDAT_STAND_EMAIL:-}
SWAP_MB=${MANDAT_STAND_SWAP_MB:-1024}
FAIL2BAN=${MANDAT_STAND_FAIL2BAN:-1}
FAIL2BAN_IGNORE=${MANDAT_STAND_FAIL2BAN_IGNORE:-}
ACTION=deploy
if [ "${1:-}" = "rollback" ]; then ACTION=rollback; shift; fi
DOMAIN=${1:-${MANDAT_STAND_DOMAIN:-}}

log() { printf '\n== %s\n' "$*"; }
die() { printf 'ошибка: %s\n' "$*" >&2; exit 1; }
warn() { printf 'предупреждение: %s\n' "$*" >&2; }
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
apt-get install -y -qq nginx certbot python3-certbot-nginx curl ca-certificates unattended-upgrades >/dev/null
if ! python_bin >/dev/null; then
  # Ubuntu 22.04: системный Python 3.10, пакету нужен 3.11+ (universe).
  apt-get install -y -qq python3.11 python3.11-venv >/dev/null || die "нужен Python 3.11+: apt-get install python3.11 python3.11-venv"
fi
PYV=$(python_bin) || die "нужен Python 3.11+"
apt-get install -y -qq "$("$PYV" -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}-venv")')" >/dev/null \
  || apt-get install -y -qq python3-venv >/dev/null

log "Система: часовой пояс, swap, журнал systemd, обновления безопасности"
# Время стенда — московское: день бюджета модели, копия в 03:15 и журналы nginx читаются без пересчёта.
if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != Europe/Moscow ]; then
  timedatectl set-timezone Europe/Moscow 2>/dev/null \
    || { ln -sf /usr/share/zoneinfo/Europe/Moscow /etc/localtime && echo Europe/Moscow > /etc/timezone; }
fi
timedatectl set-ntp true 2>/dev/null || true
MEM_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 2097152 ] && ! swapon --noheadings --show=NAME | grep -q .; then
  # Меньше 2 ГБ памяти: swap-файл страхует службу от OOM на пике (прогон корпуса, установка зависимостей).
  if [ ! -f /swapfile ]; then
    fallocate -l "${SWAP_MB}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$SWAP_MB" status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  if swapon /swapfile 2>/dev/null; then
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo 'vm.swappiness=10' > /etc/sysctl.d/90-mandat-stand.conf
    sysctl -q -p /etc/sysctl.d/90-mandat-stand.conf || true
  else
    warn "swap не включился (контейнер или файловая система без swap-файлов): стенд работает без него"
  fi
fi
# Служба пишет журнал только в journald (файлов журнала у неё нет, logrotate не нужен); журнал аудита
# journal.jsonl сам делится на сегменты и logrotate не трогается: обрезка разорвала бы цепочку хешей.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/mandat-stand.conf <<'CONF'
# Журнал systemd на маленьком сервере: не больше 200 МБ на диске и месяца истории.
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=50M
MaxRetentionSec=1month
CONF
systemctl restart systemd-journald
# Обновления безопасности ставятся сами (источники -security из 50unattended-upgrades), перезагрузку делает оператор.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
cat > /etc/apt/apt.conf.d/52mandat-stand-upgrades <<'APT'
Unattended-Upgrade::Automatic-Reboot "false";
APT

log "Код в $APP"
id mandat >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin mandat
mkdir -p "$APP" "$STATE" "$BACKUPS"
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
chown mandat:mandat "$STATE" "$BACKUPS" && chmod 700 "$STATE" "$BACKUPS"

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
      echo "MANDAT_LLM_EXTRA='$LLM_EXTRA'"
      echo "MANDAT_LLM_BUDGET_USD_PER_DAY=$LLM_BUDGET"
    } >> "$ENV_FILE"
  fi
elif grep -q '^MANDAT_LLM_API_KEY=' "$ENV_FILE"; then
  # Модель уже подключена: недостающие настройки дописываются, ключ и выбор модели остаются.
  grep -q '^MANDAT_LLM_EXTRA=' "$ENV_FILE" || echo "MANDAT_LLM_EXTRA='$LLM_EXTRA'" >> "$ENV_FILE"
  grep -q '^MANDAT_LLM_BUDGET_USD_PER_DAY=' "$ENV_FILE" || echo "MANDAT_LLM_BUDGET_USD_PER_DAY=$LLM_BUDGET" >> "$ENV_FILE"
fi
chown root:root "$ENV_FILE" && chmod 600 "$ENV_FILE"

cat > "$UNIT" <<UNIT
[Unit]
Description=Mandat demo stand (staff console)
After=network-online.target
Wants=network-online.target
# Не больше 5 запусков за 5 минут: дальше служба остаётся упавшей, и её поднимает сторож здоровья.
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
User=mandat
Group=mandat
WorkingDirectory=$APP
EnvironmentFile=$ENV_FILE
# Каталоги демо-издателя прежних запусков: ключи их списков жили в памяти процесса, списки больше не нужны.
ExecStartPre=/usr/bin/find $STATE -mindepth 1 -maxdepth 1 -type d -name mandat-stand-* -exec rm -rf {} +
ExecStart=$APP/.venv/bin/mandat serve --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=5
TimeoutStopSec=20
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

log "Сторож здоровья и ежедневная копия данных"
cat > "$WATCHDOG" <<'SH'
#!/usr/bin/env bash
# Сторож стенда «Мандата»: раз в минуту спрашивает /health и после MANDAT_STAND_WATCHDOG_FAILS неудач подряд (3)
# перезапускает службу, в том числе упавшую по пределу запусков. Остановленную оператором службу не поднимает.
set -u
port=${MANDAT_STAND_PORT:-8000}
limit=${MANDAT_STAND_WATCHDOG_FAILS:-3}
count=/run/mandat-stand-watchdog.fails
state=$(systemctl is-active mandat-stand 2>/dev/null)
if [ "$state" = inactive ]; then rm -f "$count"; exit 0; fi
if [ "$state" != failed ] && curl -fsS -m 10 -o /dev/null "http://127.0.0.1:$port/health"; then
  rm -f "$count"
  exit 0
fi
fails=$(( $(cat "$count" 2>/dev/null || echo 0) + 1 ))
if [ "$fails" -lt "$limit" ]; then
  echo "$fails" > "$count"
  echo "здоровье не ответило: неудача $fails из $limit"
  exit 0
fi
rm -f "$count"
echo "здоровье не ответило, неудач подряд: $limit; перезапуск mandat-stand"
systemctl reset-failed mandat-stand 2>/dev/null || true
systemctl restart mandat-stand
SH
chmod 755 "$WATCHDOG"
cat > /etc/systemd/system/mandat-stand-watchdog.service <<UNIT
[Unit]
Description=Mandat stand health watchdog
After=mandat-stand.service

[Service]
Type=oneshot
Environment=MANDAT_STAND_PORT=$PORT
ExecStart=$WATCHDOG
UNIT
cat > /etc/systemd/system/mandat-stand-watchdog.timer <<'UNIT'
[Unit]
Description=Mandat stand health watchdog, every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/mandat-stand-backup.service <<UNIT
[Unit]
Description=Mandat stand backup of $STATE

[Service]
Type=oneshot
User=mandat
Group=mandat
EnvironmentFile=$ENV_FILE
ExecStart=$APP/.venv/bin/mandat backup --data-dir $STATE --out $BACKUPS --keep 14 --exclude mandat-stand-*
Nice=10
IOSchedulingClass=idle
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$STATE $BACKUPS
UNIT
cat > /etc/systemd/system/mandat-stand-backup.timer <<'UNIT'
[Unit]
Description=Mandat stand backup, daily at 03:15 Moscow time

[Timer]
OnCalendar=*-*-* 03:15:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable -q mandat-stand
systemctl restart mandat-stand
systemctl enable -q --now mandat-stand-watchdog.timer mandat-stand-backup.timer

log "nginx и HTTPS для $DOMAIN"
cat > /etc/nginx/conf.d/mandat-stand-limits.conf <<'NGINX'
# Лимиты частоты по адресу клиента: API, действия консоли и тяжёлые действия (модель, прогон, шлюз).
limit_req_zone $binary_remote_addr zone=mandat_api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=mandat_heavy:10m rate=6r/m;
# Файлы страницы: при первом открытии их два десятка, а жюри часто сидит за одним NAT.
limit_req_zone $binary_remote_addr zone=mandat_static:10m rate=30r/s;
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
    location /console/static/ {
        limit_req zone=mandat_static burst=200 nodelay;
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
if [ "$FAIL2BAN" = 1 ]; then
  log "fail2ban: sshd, поток сверх лимитов nginx, подбор токена API"
  apt-get install -y -qq fail2ban python3-systemd >/dev/null
  cat > /etc/fail2ban/filter.d/mandat-stand-auth.conf <<'F2B'
# Подбор токена API стенда: ответы 401 и 403 на /v1/ и /access/v1/ в журнале доступа nginx (формат combined).
# Консоль ходит в API внутри процесса службы, её запросы сюда не попадают.
[Definition]
failregex = ^<HOST> \S+ \S+ \[\] "(?:GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS) /(?:v1|access/v1)/\S* HTTP/[\d.]+" (?:401|403)
ignoreregex =
datepattern = ^[^\[]*\[({DATE})
              {^LN-BEG}
F2B
  cat > /etc/fail2ban/jail.d/mandat-stand.conf <<JAIL
# Стенд «Мандата». Свои адреса и NAT жюри — в MANDAT_STAND_FAIL2BAN_IGNORE при запуске выкладки.
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 $FAIL2BAN_IGNORE

[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h

# Сотни отказов limit_req в минуту на API и действиях консоли — скрипт, а не человек. Зона тяжёлых действий
# (модель, прогон) и файлы страницы сюда не входят: в их лимит упираются люди за одним NAT.
[nginx-limit-req]
enabled = true
backend = auto
filter = nginx-limit-req[ngx_limit_req_zones="mandat_api"]
port = http,https
logpath = /var/log/nginx/error.log
findtime = 1m
maxretry = 300
bantime = 10m

[mandat-stand-auth]
enabled = true
backend = auto
filter = mandat-stand-auth
port = http,https
logpath = /var/log/nginx/access.log
findtime = 10m
maxretry = 20
bantime = 1h
JAIL
  if fail2ban-client -t >/dev/null 2>&1; then
    systemctl enable -q fail2ban
    systemctl restart fail2ban
  else
    warn "настройка fail2ban не прошла fail2ban-client -t: правила стенда сняты, остальное работает"
    rm -f /etc/fail2ban/jail.d/mandat-stand.conf
    systemctl restart fail2ban || true
  fi
fi

self_check
if [ "${URL#https}" != "$URL" ]; then
  curl -fsSI "$URL" | grep -qi '^strict-transport-security' || die "HTTPS без HSTS: проверьте nginx"
fi
echo
systemctl is-active --quiet mandat-stand-watchdog.timer || die "сторож здоровья не запущен: systemctl status mandat-stand-watchdog.timer"
systemctl start mandat-stand-backup.service || die "копия данных не снята: journalctl -u mandat-stand-backup -n 50"
echo
echo "Стенд:   $URL"
echo "Здоровье: ${URL%/console/}/health"
echo "Журнал службы: journalctl -u mandat-stand -f"
echo "Копии данных: $BACKUPS (каждый день в 03:15 МСК, последние 14)"
