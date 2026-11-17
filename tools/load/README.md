# Нагрузочный стенд API

`run_load.py` поднимает `mandat serve` отдельным процессом и гоняет его клиентом на asyncio + httpx: каждый виртуальный пользователь в цикле отправляет `POST /v1/cases` (разбор и проверка кейса из синтетического корпуса, 49 видов дефектов) и читает отчёт `GET /v1/cases/{id}`. После прогона проверяется целостность журнала решений.

```bash
pip install -e ".[dev,api]"
python tools/load/run_load.py --store memory --stages 1,4,16,32,64 --stage-seconds 10 --overload
python tools/load/run_load.py --store sqlite --stages 1,4,16,32
python tools/load/run_load.py --store redis://127.0.0.1:6379/0      # если есть Redis
python tools/load/run_load.py --stages 1 --soak-seconds 180         # выдержка, рост памяти
python tools/load/run_load.py --smoke                               # CI: 4 клиента 5 с, 0 ошибок, журнал цел
```

Что меряется: RPS, p50/p95/p99 полного цикла «создать + прочитать», коды ответов, RSS сервера, число записей журнала и его проверка. `--overload` поднимает второй сервер с лимитом 120 запросов в минуту и проверяет, что лишнее отсекается 429, а не ошибками.

Ограничения: клиент и сервер на одной машине делят CPU; один процесс uvicorn; сеть — loopback. Цифры годятся для сравнения версий и поиска узких мест, а не как SLA. Результаты: [docs/testing/LOAD_REPORT-2026-10-14.md](../../docs/testing/LOAD_REPORT-2026-10-14.md).
