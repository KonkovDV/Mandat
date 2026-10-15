"""Командная строка «Мандата».

mandat verify examples/cases/01_ok --html var/01.html
mandat demo                      # все демо-кейсы: отчёты, журнал, удостоверения, списки статусов в var/demo
mandat monitor examples/events/takeover.json --status-dir var/demo/status
mandat attest-verify var/demo/attestations/01_ok.json --public-key-file var/demo/public_key.txt \
    --status-dir var/demo/status
mandat eval --per-defect 6
mandat journal-verify var/demo/journal.jsonl
mandat journal-proof var/demo/journal.jsonl --seq 3
mandat formats                   # поддерживаемые форматы МЧД
mandat examples                  # пересобрать examples/ из генератора
mandat serve --port 8000         # нужен extra [api]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from mandat import ENGINE
from mandat.audit import attestation, journal, merkle
from mandat.audit.status_list import StatusRegistry, check_status
from mandat.intake.case_loader import CaseError, load_case_dir
from mandat.report.html import render
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case
from mandat.services.webhooks import delivery

EXIT = {"confirmed": 0, "needs_review": 10, "insufficient_data": 11, "refuted": 12}


def _repo_root() -> Path:
    here = Path.cwd()
    for candidate in (here, *here.parents):
        if (candidate / "examples" / "cases").is_dir():
            return candidate
    return here


def _write(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        case = load_case_dir(Path(args.case))
    except CaseError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    report = verify_case(case, profile=args.profile).as_dict()
    _write(Path(args.json) if args.json else None, json.dumps(report, ensure_ascii=False, indent=2))
    _write(Path(args.html) if args.html else None, render(report))
    if args.journal:
        journal.append(
            Path(args.journal),
            action="verify",
            actor="cli",
            role="operator",
            case_id=report["case_id"],
            payload_hash=report["reproducibility_hash"],
            details={"verdict": report["verdict"]},
        )
    print(f"{report['case_id']}: {report['verdict_label']}")
    for step in report["next_steps"]:
        print(f"  → {step['action']} ({step['unit']})")
    if not args.json:
        print(json.dumps({"verdict": report["verdict"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return EXIT[report["verdict"]] if args.exit_code else 0


def cmd_demo(args: argparse.Namespace) -> int:
    root = _repo_root()
    cases = sorted((root / "examples" / "cases").iterdir())
    out = Path(args.out)
    log = out / "journal.jsonl"
    if log.exists():
        previous = out / "previous"
        previous.mkdir(parents=True, exist_ok=True)
        stamp = log.stat().st_mtime_ns
        log.replace(previous / f"journal-{stamp}.jsonl")
        head = log.with_name(log.name + ".head")
        if head.exists():
            head.replace(previous / f"journal-{stamp}.jsonl.head")
    from mandat.audit.signing import SignerError, signer_from_env

    try:
        key = signer_from_env(out / "keys" / "attestation_ed25519.pem")
    except SignerError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    pseudonym_key = attestation.load_or_create_pseudonym_key(out / "keys" / "representative_hmac.key")
    status_dir = out / "status"
    for stale in ("registry.json", "revocation.json", "suspension.json"):
        (status_dir / stale).unlink(missing_ok=True)
    status = StatusRegistry.open(status_dir)
    mismatches = 0
    print(f"{'кейс':32s} {'ожидалось':20s} получено")
    for case_dir in cases:
        manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        report = verify_case(load_case_dir(case_dir), profile=args.profile).as_dict()
        _write(out / f"{case_dir.name}.html", render(report))
        _write(out / f"{case_dir.name}.json", json.dumps(report, ensure_ascii=False, indent=2))
        index = status.allocate(
            {
                "case_id": report["case_id"],
                "org_inn": report["organization"]["inn"],
                "verdict": report["verdict"],
                "poa_numbers": report["chain"]["poa_numbers"],
                "attestation": f"attestations/{case_dir.name}.json",
            }
        )
        cred = attestation.issue(report, key, status=status.credential_status(index), pseudonym_key=pseudonym_key)
        _write(out / "attestations" / f"{case_dir.name}.json", attestation.dump(cred))
        journal.append(
            log,
            action="verify",
            actor="demo",
            role="operator",
            case_id=report["case_id"],
            payload_hash=report["reproducibility_hash"],
            details={"verdict": report["verdict"]},
        )
        expected = manifest.get("expected_verdict")
        flag = "" if args.profile != "demo" or expected == report["verdict"] else "  ← НЕ СОВПАЛО"
        mismatches += bool(flag)
        print(f"{case_dir.name:32s} {str(expected):20s} {report['verdict_label']}{flag}")
    status.save(key)
    check = journal.verify_journal(log)
    _write(out / "public_key.txt", attestation.public_key_multibase(key) + "\n")
    tree = merkle.root(merkle.journal_leaves(journal.read_journal(log))).hex()
    print(f"журнал: {check.records} записей, целостность {'OK' if check.ok else 'НАРУШЕНА: ' + check.reason}")
    print(f"корень дерева Меркла журнала: {tree}")
    print(f"отчёты: {out}/*.html, удостоверения: {out}/attestations/, списки статусов: {status_dir}/")
    return 1 if mismatches or not check.ok else 0


def cmd_monitor(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.events).read_text(encoding="utf-8"))
    rights, events = parse_events(payload)
    alerts = evaluate(rights, events, window_hours=args.window_hours)
    result: dict[str, Any] = {"alerts": [a.as_dict() for a in alerts]}
    if args.status_dir:
        status_dir = Path(args.status_dir)
        from mandat.audit.signing import SignerError, signer_from_env

        key_path = Path(args.key) if args.key else status_dir.parent / "keys" / "attestation_ed25519.pem"
        external = os.environ.get("MANDAT_SIGNER", "file").strip().lower() not in ("", "file")
        if not (external or key_path.exists()) or not (status_dir / "registry.json").exists():
            print("нет списков статусов или ключа: сначала выполните mandat demo", file=sys.stderr)
            return 2
        try:
            key = signer_from_env(key_path, create=False)
        except SignerError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 2
        registry = StatusRegistry.open(status_dir)
        changes = []
        for alert in alerts:
            if not alert.attestation_action:
                continue
            number = alert.poa_number if alert.attestation_action == "revocation" else None
            for index in registry.find(org_inn=alert.org_inn, poa_number=number):
                if registry.mark(index, alert.attestation_action, alert.kind):
                    meta = registry.entries[str(index)]
                    changes.append({"attestation": meta.get("attestation"), "status": alert.attestation_action})
                    if args.journal:
                        journal.append(
                            Path(args.journal),
                            action="status_change",
                            actor="monitor",
                            role="security",
                            case_id=str(meta.get("case_id")),
                            payload_hash=str(index),
                            reason=alert.kind,
                            details={"status": alert.attestation_action},
                        )
        registry.save(key)
        result["status_changes"] = changes
    secret = os.environ.get("MANDAT_WEBHOOK_SECRET", "").strip()
    if secret and result["alerts"]:
        try:
            result["webhook"] = delivery({"alerts": result["alerts"]}, secret)
        except ValueError as exc:
            print(f"ошибка: MANDAT_WEBHOOK_SECRET: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from mandat.eval.run import run_corpus

    metrics = run_corpus(args.per_defect, seed=args.seed, profile=args.profile)
    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    _write(Path(args.out) if args.out else None, text)
    print(text)
    return 0 if metrics["false_confirmed"] == 0 and not metrics["mismatches"] else 1


def cmd_journal_verify(args: argparse.Namespace) -> int:
    check = journal.verify_journal(Path(args.path), anchor=args.anchor)
    if not check.ok and check.records == 0 and check.broken_at:
        print(json.dumps(check.as_dict(), ensure_ascii=False))
        return 1
    tree = merkle.root(merkle.journal_leaves(journal.read_journal(Path(args.path)))).hex()
    result = {**check.as_dict(), "merkle_root": tree}
    ok = check.ok
    if args.merkle_anchor is not None and args.merkle_anchor.strip().lower() != tree:
        ok = False
        result.update(ok=False, reason="корень Меркла не совпадает с сохранённым вне журнала")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


def cmd_journal_proof(args: argparse.Namespace) -> int:
    records = journal.read_journal(Path(args.path))
    leaves = merkle.journal_leaves(records)
    index = args.seq - 1
    if not 0 <= index < len(leaves):
        print(f"в журнале {len(leaves)} записей, записи {args.seq} нет", file=sys.stderr)
        return 2
    proof = merkle.inclusion_proof(index, leaves)
    tree = merkle.root(leaves)
    ok = merkle.verify_inclusion(leaves[index], index, len(leaves), proof, tree)
    print(
        json.dumps(
            {
                "seq": args.seq,
                "size": len(leaves),
                "record": records[index],
                "leaf_hash": leaves[index].hex(),
                "proof": [h.hex() for h in proof],
                "root": tree.hex(),
                "verified": ok,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def cmd_formats(args: argparse.Namespace) -> int:
    from mandat.intake.formats import describe

    print(json.dumps(describe(), ensure_ascii=False, indent=2))
    return 0


def _attest_verify_keyring(args: argparse.Namespace, cred: dict[str, Any]) -> int:
    """Проверка по связке ключей издателя: ключ выбирается по kid из подписанного доказательства."""
    from datetime import UTC, datetime

    from mandat.audit.signing import Keyring
    from mandat.services.env_policy import protected, require_attestation_status

    try:
        keyring = Keyring.load(Path(args.keyring))
    except (OSError, ValueError) as exc:
        print(f"ошибка: связка ключей не читается: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "crypto_only", False):
        problems = attestation.verify_cryptographic(cred, keyring, now=datetime.now(UTC), allow_demo=not protected())
        for problem in problems:
            print(f"криптопроверка: {problem}", file=sys.stderr)
        if problems:
            return 4
        print("подпись и поля удостоверения верны (только криптография, допуск операции не подтверждён)")
        print(f"вердикт в удостоверении: {(cred.get('credentialSubject') or {}).get('verdict')}")
        return 0
    status_dir = Path(args.status_dir) if args.status_dir else None
    if require_attestation_status() and status_dir is None:
        print("ошибка: в защищённом режиме нужен --status-dir (или --crypto-only для диагностики)", file=sys.stderr)
        return 2
    problems = attestation.validate_attestation(
        cred,
        keyring,
        now=datetime.now(UTC),
        status_dir=status_dir,
        allow_demo=not protected(),
        require_status=require_attestation_status(),
        operation_risk=getattr(args, "operation_risk", None) or None,
    )
    for problem in problems:
        print(f"не принимается: {problem}", file=sys.stderr)
    if problems:
        # 3 — статус не «действует» (отзыв/приостановка/устарел); 4 — прочие поля/крипто.
        if any("статус удостоверения:" in p for p in problems):
            return 3
        return 4
    print("подпись верна, удостоверение подлинно")  # после всех проверок
    print(f"вердикт в удостоверении: {(cred.get('credentialSubject') or {}).get('verdict')}")
    print("допуск операции: отдельно через authorize_operation / банковский контур")
    return 0


def cmd_attest_verify(args: argparse.Namespace) -> int:
    cred = json.loads(Path(args.path).read_text(encoding="utf-8"))
    method = cred.get("proof", {}).get("verificationMethod", "")
    if args.keyring:
        return _attest_verify_keyring(args, cred)
    trusted = args.public_key
    if trusted is None and args.public_key_file:
        trusted = Path(args.public_key_file).read_text(encoding="utf-8").strip()
    if trusted is None and not args.trust_embedded_key:
        # Ключ из самого удостоверения доказывает только, что его подписал тот, кто его же и вписал.
        print("ошибка: ключ издателя не задан: передайте --public-key или --public-key-file", file=sys.stderr)
        return 2
    if trusted is None:
        print("предупреждение: ключ взят из самого удостоверения, издатель не проверен", file=sys.stderr)
    key = attestation.public_key_from_multibase(trusted or method.split("#", 1)[-1])
    ok = attestation.verify_signature(cred, key)
    if not ok:
        print("подпись НЕ верна", file=sys.stderr)
        return 1
    # «подпись верна» печатаем только после всех проверок, чтобы exit 2/4 не читался как успех.
    from datetime import UTC, datetime

    from mandat.services.env_policy import protected, require_attestation_status

    if getattr(args, "crypto_only", False):
        problems = attestation.verify_cryptographic(cred, [key], now=datetime.now(UTC), allow_demo=not protected())
        for problem in problems:
            print(f"криптопроверка: {problem}", file=sys.stderr)
        if problems:
            return 4
        print("подпись верна (только подпись)")
        print("поля удостоверения верны (только криптография, допуск операции не подтверждён)")
        print(f"вердикт в удостоверении: {(cred.get('credentialSubject') or {}).get('verdict')}")
        return 0

    status_dir = Path(args.status_dir) if args.status_dir else None
    must_status = require_attestation_status()
    if must_status and status_dir is None:
        print("ошибка: в защищённом режиме нужен --status-dir (или --crypto-only для диагностики)", file=sys.stderr)
        return 2

    # В pilot/prod статус (в т.ч. свежесть списка) входит в validate. На стенде status_dir
    # проверяется отдельно через check_status без now — демо-списки можно смотреть через месяц.
    operation_risk = getattr(args, "operation_risk", None) or None
    problems = attestation.validate_attestation(
        cred,
        [key],
        now=datetime.now(UTC),
        status_dir=status_dir if must_status else None,
        allow_demo=not protected(),
        require_status=must_status,
        operation_risk=operation_risk,
    )
    for problem in problems:
        print(f"не принимается: {problem}", file=sys.stderr)
    if problems:
        # 3 — статус не «действует» (отзыв/приостановка/устарел); 4 — прочие поля/крипто.
        if any("статус удостоверения:" in p for p in problems):
            return 3
        return 4
    # Действительное удостоверение бывает и у опровергнутого кейса: вердикт печатается всегда.
    verdict = (cred.get("credentialSubject") or {}).get("verdict")
    print("подпись верна")
    print(f"вердикт в удостоверении: {verdict}")
    print("удостоверение подлинно; допуск операции — отдельно (authorize_operation)")
    if status_dir is None and cred.get("credentialStatus"):
        print("предупреждение: статус не проверялся, передайте --status-dir", file=sys.stderr)
    if status_dir is not None:
        seen_path = Path(args.seen_file) if args.seen_file else None
        seen = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path and seen_path.exists() else {}
        try:
            status = check_status(
                cred,
                status_dir,
                key,
                seen=seen if seen_path else None,
                now=datetime.now(UTC) if must_status else None,
                operation_risk=operation_risk,
                require_seen=False,
            )
        except (OSError, ValueError, KeyError) as exc:
            print(f"статус не проверен: {exc}", file=sys.stderr)
            return 1
        if seen_path:
            seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"статус удостоверения: {status['state']}")
        return 0 if status["state"] == "действует" else 3
    return 0


def cmd_examples(args: argparse.Namespace) -> int:
    from mandat.examples import write_examples

    for path in write_examples(Path(args.out)):
        print(path)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from mandat.api.principals import access_problem
    from mandat.audit.anchor import missing_anchor_problem
    from mandat.services.env_policy import key_problem

    try:
        problem = access_problem() or key_problem() or missing_anchor_problem()
    except ValueError as exc:
        problem = str(exc)
    if problem:
        print(f"ошибка: {problem}", file=sys.stderr)
        return 2
    try:
        import uvicorn
    except ModuleNotFoundError:
        print("установите extra: pip install -e '.[api]'", file=sys.stderr)
        return 2
    from mandat.api.storage import open_store

    try:
        store = open_store()
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    if args.workers > 1 and store.name == "memory":
        print(
            "ошибка: у нескольких воркеров память не общая, задайте MANDAT_API_STORE=sqlite:///… или redis://…",
            file=sys.stderr,
        )
        return 2
    options = server_options()
    if args.workers > 1:
        uvicorn.run(
            "mandat.api.app:create_app", factory=True, host=args.host, port=args.port, workers=args.workers, **options
        )
    else:
        from mandat.api.app import create_app

        uvicorn.run(create_app(store=store), host=args.host, port=args.port, **options)
    return 0


def server_options() -> dict[str, Any]:
    """Защита сервера от перегрузки (OWASP API4:2023, R2-API-2).

    ``limit_concurrency`` — сверх этого числа одновременных соединений uvicorn сразу отвечает 503, а не
    копит очередь до таймаутов; ``timeout_keep_alive`` закрывает простаивающие соединения; заголовок
    ``server`` не раскрывает сервер и его версию.
    """
    raw = os.environ.get("MANDAT_API_MAX_CONCURRENCY", "").strip()
    limit = int(raw) if raw.isdigit() and int(raw) > 0 else 64
    return {
        "limit_concurrency": limit,
        "timeout_keep_alive": 5,
        "server_header": False,
        "proxy_headers": False,
        # TCP_NODELAY и при --workers N: иначе +40 мс на запрос по keep-alive (R2-PERF-3, api/nodelay.py).
        "http": "mandat.api.nodelay:NoDelayH11Protocol",
    }


def cmd_webhook_secret(args: argparse.Namespace) -> int:
    from mandat.services.webhooks import new_secret, new_signing_key

    if args.asymmetric:
        signing, verifying = new_signing_key()
        print(f"{signing}  # ключ подписи: только отправителю (MANDAT_WEBHOOK_SECRET)")
        print(f"{verifying}  # ключ проверки: получателю, он не секретный")
        return 0
    print(new_secret())
    return 0


def cmd_api_user(args: argparse.Namespace) -> int:
    from mandat.api.principals import ROLES, hash_token

    if args.role not in ROLES:
        print(f"роль: одна из {', '.join(sorted(ROLES))}", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    entry = {"name": args.name, "role": args.role, "token_sha256": hash_token(token)}
    print(f"токен для {args.name} (показывается один раз): {token}")
    print("строка для MANDAT_API_USERS:")
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mandat", description=f"{ENGINE}: проверка полномочий представителя юрлица")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="проверить кейс из каталога с case.json")
    v.add_argument("case")
    v.add_argument("--profile", default=None, choices=["demo", "strict"])
    v.add_argument("--json")
    v.add_argument("--html")
    v.add_argument("--journal")
    v.add_argument("--exit-code", action="store_true", help="код выхода по вердикту (0/10/11/12)")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("demo", help="прогнать демо-кейсы")
    d.add_argument("--out", default="var/demo")
    d.add_argument("--profile", default="demo", choices=["demo", "strict"])
    d.set_defaults(func=cmd_demo)

    m = sub.add_parser("monitor", help="оценить события по выданным правам")
    m.add_argument("events")
    m.add_argument("--window-hours", type=int, default=72)
    m.add_argument("--status-dir", help="каталог списков статусов: отозвать или приостановить удостоверения")
    m.add_argument("--key", help="ключ подписи списков (по умолчанию ../keys/attestation_ed25519.pem)")
    m.add_argument("--journal", help="записать изменения статусов в журнал")
    m.set_defaults(func=cmd_monitor)

    e = sub.add_parser("eval", help="синтетический корпус и метрики")
    e.add_argument("--per-defect", type=int, default=3)
    e.add_argument("--seed", type=int, default=7)
    e.add_argument("--profile", default="demo", choices=["demo", "strict"])
    e.add_argument("--out")
    e.set_defaults(func=cmd_eval)

    j = sub.add_parser("journal-verify", help="проверить целостность журнала")
    j.add_argument("path")
    j.add_argument("--anchor", help="последний хеш журнала, сохранённый вне журнала")
    j.add_argument("--merkle-anchor", help="корень Меркла, сохранённый вне журнала (например, в СЭД или SIEM)")
    j.set_defaults(func=cmd_journal_verify)

    jp = sub.add_parser("journal-proof", help="доказательство включения записи в журнал (дерево Меркла)")
    jp.add_argument("path")
    jp.add_argument("--seq", type=int, required=True, help="номер записи, с 1")
    jp.set_defaults(func=cmd_journal_proof)

    f = sub.add_parser("formats", help="поддерживаемые форматы МЧД")
    f.set_defaults(func=cmd_formats)

    a = sub.add_parser("attest-verify", help="проверить подпись, срок, издателя и статус удостоверения")
    a.add_argument("path")
    a.add_argument("--public-key", help="ключ издателя в multibase (public_key.txt рядом с удостоверениями)")
    a.add_argument("--public-key-file", help="файл с ключом издателя, например var/demo/public_key.txt")
    a.add_argument("--trust-embedded-key", action="store_true", help="взять ключ из удостоверения (только для отладки)")
    a.add_argument("--keyring", help="связка ключей издателя (JSON keys[kid,status,retired_at]), см. docs/KEYS.md")
    a.add_argument("--seen-file", help="состояние проверяющего: наибольший виденный sequence каждого списка")
    a.add_argument("--status-dir", help="каталог списков статусов W3C Bitstring Status List")
    a.add_argument(
        "--crypto-only",
        action="store_true",
        help="только подпись и поля: без статуса и без формулировки принятия/допуска",
    )
    a.add_argument(
        "--operation-risk",
        default=None,
        help="риск операции для окна свежести списка (payment/archive_receipt/…); см. status_max_age_for",
    )
    a.set_defaults(func=cmd_attest_verify)

    x = sub.add_parser("examples", help="пересобрать демо-кейсы")
    x.add_argument("--out", default="examples/cases")
    x.set_defaults(func=cmd_examples)

    u = sub.add_parser("api-user", help="выпустить токен пользователя API и его хеш")
    u.add_argument("--name", required=True)
    u.add_argument("--role", required=True, help="operator, reviewer, auditor или admin")
    u.set_defaults(func=cmd_api_user)

    s = sub.add_parser("serve", help="запустить HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1, help="больше одного нужен MANDAT_API_STORE")
    s.set_defaults(func=cmd_serve)
    w = sub.add_parser("webhook-secret", help="новый секрет вебхука: whsec_ (HMAC) или пара whsk_/whpk_ (Ed25519)")
    w.add_argument("--asymmetric", action="store_true", help="пара ключей Ed25519 для подписи v1a")
    w.set_defaults(func=cmd_webhook_secret)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except journal.JournalKeyMissing as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        # Ожидаемые ошибки входа — одна строка по-русски и код 2, без трассировки.
        detail = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"ошибка: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
