# NOTICE

«Мандат» © 2026 Коньков Дмитрий, лицензия MIT (`LICENSE`). Ниже — откуда взят перенесённый код и под какими лицензиями сторонние зависимости.

## Перенесённый код

Часть модулей перенесена с изменениями из других репозиториев того же автора. Все они под MIT (проверено по GitHub 13.10.2026), построчно — в [PORTING](docs/PORTING.md):

- AeroBIM — свёртка вердикта, происхождение находок, безопасный XML, пакет правил, машина состояний разбора, псевдонимизация, клиент модели, статистика оценки;
- SynAPS-ProgramPlan — журнал решений, пояснение по фактам, реестр утверждений, `evidence.json`;
- fsp-talent — удостоверение W3C VC с подписью Ed25519, контрольное число ИНН, CI;
- OpenRebar — трассировка «норма → реализация → тест»;
- ещё два репозитория автора, которые здесь не названы, — набор доказательств, границы утверждений, роль рецензента, правило вето, принцип «ненайденное значение не равно соответствию». Их лицензии не проверены.

## Зависимости

Лицензии сверены по метаданным пакетов 13.10.2026.

| Пакет | Зачем | Лицензия |
|---|---|---|
| `defusedxml` | ядро | PSF |
| `cryptography` | ядро | Apache-2.0 или BSD-3-Clause |
| `fastapi`, `uvicorn` | extra `api` | MIT, BSD-3-Clause |
| `redis` | extra `redis` | MIT |
| `cedarpy` | extra `cedar`, только тесты | Apache-2.0 |
| `pytest`, `ruff`, `mypy` | разработка | MIT |
| `httpx`, `fakeredis` | разработка | BSD-3-Clause |

Дерево Меркла построено по RFC 6962 и RFC 9162, списки статусов — по W3C Bitstring Status List v1.0.

## Тестовые векторы

`tests/vectors/eddsa-jcs-2022/` — копия векторов `eddsa-jcs-2022` из
[w3c/vc-di-eddsa](https://github.com/w3c/vc-di-eddsa) на коммите
`13e7070534d1ba1c2987cb26bfb1535e0f15bb72`, без правок. Лицензия — W3C Software and
Document License, текст в `tests/vectors/eddsa-jcs-2022/LICENSE.md`.

This repository includes material copied from Data Integrity EdDSA Cryptosuites
(https://github.com/w3c/vc-di-eddsa). Copyright © 2023 World Wide Web Consortium.
https://www.w3.org/copyright/software-license-2023/
