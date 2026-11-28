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
mandat backup --data-dir /var/lib/mandat-stand --out /var/backups/mandat-stand   # копия без остановки службы
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mandat import ENGINE
from mandat.audit import attestation, journal, merkle
from mandat.audit.status_list import StatusRegistry, check_status
from mandat.intake.case_loader import CaseError, load_case_dir
from mandat.report.html import render
from mandat.rules.pack import default_pack
from mandat.services.monitoring import MOSCOW, NOTICE_DAYS, evaluate, parse_as_of, parse_events
from mandat.services.verify import verify_case
from mandat.services.webhooks import delivery
from mandat.strictjson import strict_loads

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
        manifest = strict_loads((case_dir / "case.json").read_text(encoding="utf-8"))
        report = verify_case(load_case_dir(case_dir), profile=args.profile).as_dict()
        _write(out / f"{case_dir.name}.html", render(report))
        _write(out / f"{case_dir.name}.json", json.dumps(report, ensure_ascii=False, indent=2))
        index = status.allocate(
            {
                "case_id": report["case_id"],
                "org_inn": report["organization"]["inn"],
                "verdict": report["verdict"],
                "poa_numbers": report["chain"]["poa_numbers"],
                "chain_kind": report["chain"]["kind"],
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
    payload = strict_loads(Path(args.events).read_text(encoding="utf-8"))
    rights, events = parse_events(payload)
    # Сроки МЧД считаются на момент --as-of, иначе на as_of из пачки, иначе на текущее время.
    as_of = parse_as_of({"as_of": args.as_of} if args.as_of else payload) or datetime.now(UTC)
    alerts = evaluate(rights, events, window_hours=args.window_hours, as_of=as_of, notice_days=args.notice_days)
    result: dict[str, Any] = {"alerts": [a.as_dict() for a in alerts]}
    if any(r.poa_valid_until for r in rights):
        result["as_of"] = as_of.astimezone(MOSCOW).isoformat()
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
            for index in registry.find(org_inn=alert.org_inn, poa_number=number, chain_kind=alert.chain_kind):
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
    from mandat.eval import repeat
    from mandat.eval.run import run_corpus

    metrics = run_corpus(args.per_defect, seed=args.seed, profile=args.profile)
    # Повторные клиенты: доля платежей к человеку без реестра заключений юриста и с ним (eval/repeat.py).
    clients = repeat.run().as_dict()
    metrics["repeat_clients"] = clients
    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    _write(Path(args.out) if args.out else None, text)
    print(text)
    clean = clients["false_confirmed_with_registry"] == 0
    return 0 if metrics["false_confirmed"] == 0 and not metrics["mismatches"] and clean else 1


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


def cmd_law_calendar(args: argparse.Namespace) -> int:
    """Календарь норм пакета: какие акты вступают в силу или утрачивают её в окне и какие правила на них стоят.

    Код возврата 1 — на дату ``--on`` уже есть акт, утративший силу, а правила на нём не пересверены.
    Без ``--on`` — сегодняшний день банка по Москве, а не день часового пояса сервера.
    """
    from datetime import date, timedelta

    from mandat.rules.pack import default_pack

    try:
        on = date.fromisoformat(args.on) if args.on else attestation.bank_date(datetime.now(UTC))
    except ValueError:
        print(f"--on: ожидалась дата ГГГГ-ММ-ДД, получено {args.on!r}", file=sys.stderr)
        return 2
    until = on + timedelta(days=max(0, args.days))
    rows = []
    for norm in default_pack().norms:
        events = [
            {"event": label, "date": day.isoformat()}
            for label, day in (("вступает в силу", norm.in_force_from), ("утрачивает силу", norm.in_force_until))
            if day is not None and on <= day <= until
        ]
        rows.append(
            {
                "id": norm.id,
                "title": norm.title,
                "state": norm.state_on(on),
                "in_force_from": norm.in_force_from.isoformat() if norm.in_force_from else None,
                "in_force_until": norm.in_force_until.isoformat() if norm.in_force_until else None,
                "events": events,
                "rules": list(norm.rules),
                "url": norm.url,
            }
        )
    stale = [r["id"] for r in rows if r["state"] == "утратил силу"]
    report = {"on": on.isoformat(), "days": args.days, "stale": stale, "norms": rows}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if stale else 0


def _attest_verify_keyring(args: argparse.Namespace, cred: dict[str, Any]) -> int:
    """Проверка по связке ключей издателя: ключ выбирается по kid из подписанного доказательства."""

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
    try:
        cred = strict_loads(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("ошибка: удостоверение не является строгим JSON", file=sys.stderr)
        return 2
    if not isinstance(cred, dict):
        print("ошибка: удостоверение должно быть объектом JSON", file=sys.stderr)
        return 2
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
    # Статус проверяется ДО любого «успешного» вывода: на стенде validate его не включает (стенд смотрит
    # демо-списки без now), и отозванное удостоверение не должно сначала напечатать «подпись верна».
    status_state: str | None = None
    seen_path = Path(args.seen_file) if status_dir is not None and args.seen_file else None
    seen: dict[str, Any] = {}
    if status_dir is not None:
        try:
            seen = strict_loads(seen_path.read_text(encoding="utf-8")) if seen_path and seen_path.exists() else {}
            if not isinstance(seen, dict):
                raise ValueError("файл seen должен быть объектом JSON")
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
        status_state = str(status["state"])
        if status_state != "действует":
            print(f"не принимается: статус удостоверения: {status_state}", file=sys.stderr)
            return 3
    # Действительное удостоверение бывает и у опровергнутого кейса: вердикт печатается всегда.
    verdict = (cred.get("credentialSubject") or {}).get("verdict")
    print("подпись верна")
    print(f"вердикт в удостоверении: {verdict}")
    print("удостоверение подлинно; допуск операции — отдельно (authorize_operation)")
    if status_dir is None and cred.get("credentialStatus"):
        print("предупреждение: статус не проверялся, передайте --status-dir", file=sys.stderr)
    if status_state is not None:
        print(f"статус удостоверения: {status_state}")
    return 0


def cmd_approvals_verify(args: argparse.Namespace) -> int:
    """Проверить файл утверждений правил: каждая запись — действует или почему нет."""
    from mandat.rules.approvals import ApprovalError, check_record, load_approvals

    try:
        loaded = load_approvals(Path(args.file) if args.file else None, Path(args.keys) if args.keys else None)
        moment = datetime.fromisoformat(args.at) if args.at else datetime.now(UTC)
        if moment.tzinfo is None:
            raise ApprovalError("--at: дата и время с часовым поясом")
    except (ApprovalError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    pack = default_pack()
    rejected = 0
    for index, record in enumerate(loaded.records):
        reason = check_record(record, pack, loaded.keyring, moment)
        rule = record.get("rule_id") if isinstance(record, dict) else None
        print(f"{index}: {str(rule)[:40] if rule else '?'}: {'действует' if reason is None else reason}")
        rejected += reason is not None
    print(f"записей: {len(loaded.records)}, действуют: {len(loaded.records) - rejected}; источник: {loaded.source}")
    return 1 if rejected else 0


def cmd_approvals_sign(args: argparse.Namespace) -> int:
    """Стенд: подписать запись утверждения ключом PEM. В банке запись подписывает КЭП юриста через СКЗИ."""
    from mandat.audit.signing import SignerError, load_pem_signer
    from mandat.rules.approvals import sign_record

    pack = default_pack()
    if args.rule not in pack.rules:
        print(f"ошибка: правила {args.rule} нет в пакете", file=sys.stderr)
        return 2
    try:
        signer = load_pem_signer(Path(args.key), create=False)
        record = sign_record(
            pack.rules[args.rule],
            pack.pack_id,
            signer,
            approver_role=args.role,
            norm_edition=args.norm_edition,
            norm_checked_on=date.fromisoformat(args.checked_on),
            valid_until=date.fromisoformat(args.valid_until),
        )
    except (SignerError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    print(f"ключ утверждающего: {signer.key_id}", file=sys.stderr)
    if not args.file:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    target = Path(args.file)
    payload = strict_loads(target.read_bytes()) if target.exists() else {"schema": 1, "approvals": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("approvals"), list):
        print(f"ошибка: {target} — не файл утверждений", file=sys.stderr)
        return 2
    payload["approvals"].append(record)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"запись добавлена в {target}", file=sys.stderr)
    return 0


def cmd_showcase(args: argparse.Namespace) -> int:
    """Сценарий «захват компании останавливается до платежа»: проверка, удостоверение, допуск, захват, отказ."""
    from mandat.services import showcase
    from mandat.services.env_policy import protected

    if protected():
        print(
            "ошибка: showcase — сценарий стенда на вымышленных данных; в защищённом режиме не запускается",
            file=sys.stderr,
        )
        return 2
    from mandat.eval import repeat

    show = showcase.run(_repo_root(), Path(args.out))
    print(showcase.render_text(show))
    print(f"Шаги и решения: {Path(args.out) / 'showcase.json'}, журнал: {Path(args.out) / 'journal.jsonl'}")
    # Второй сценарий — повторный клиент и реестр заключений юриста; свой журнал и свой файл итогов.
    clients = repeat.run(journal_path=Path(args.out) / "repeat" / "journal.jsonl").as_dict()
    repeat.write(clients, Path(args.out) / "repeat_clients.json")
    print()
    print(repeat.render_text(clients))
    print(f"Итоги: {Path(args.out) / 'repeat_clients.json'}, журнал: {Path(args.out) / 'repeat' / 'journal.jsonl'}")
    refused_last = show.steps[-1].outcome == "ОТКАЗ"
    registry_ok = clients["journal"]["intact"] and clients["false_confirmed_with_registry"] == 0
    return 0 if refused_last and show.journal_ok and show.proof_ok and registry_ok else 1


def cmd_examples(args: argparse.Namespace) -> int:
    from mandat.examples import write_examples

    for path in write_examples(Path(args.out)):
        print(path)
    return 0


def _stand_access_problem() -> str | None:
    """На стенде учётные записи консоли создаёт сам сервер; ``MANDAT_API_USERS``, если задан, должен читаться."""
    from mandat.api.principals import load_users

    try:
        load_users()
    except (ValueError, OSError) as exc:
        return f"MANDAT_API_USERS не читается: {exc}"
    return None


def cmd_serve(args: argparse.Namespace) -> int:
    from mandat import console
    from mandat.api.principals import access_problem
    from mandat.audit.anchor import missing_anchor_problem
    from mandat.services.env_policy import key_problem, require_attestation_status

    try:
        require_attestation_status()  # выключенная проверка статуса в pilot/prod — ошибка конфигурации
        access = _stand_access_problem() if console.requested() else access_problem()
        problem = console.problem() or access or key_problem() or missing_anchor_problem()
    except ValueError as exc:
        problem = str(exc)
    if problem:
        print(f"ошибка: {problem}", file=sys.stderr)
        return 2
    if console.requested() and args.workers > 1:
        print("ошибка: консоль стенда держит сессии в памяти процесса и работает одним воркером", file=sys.stderr)
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
    if args.workers == 0:
        args.workers = os.cpu_count() or 1  # по числу процессоров: воркеры не делят GIL
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
    """Защита сервера от перегрузки (OWASP API4:2023).

    ``limit_concurrency`` — сверх этого числа одновременных соединений uvicorn сразу отвечает 503, а не
    копит очередь до таймаутов; ``timeout_keep_alive`` закрывает простаивающие соединения; заголовок
    ``server`` не раскрывает сервер и его версию.
    """
    raw = os.environ.get("MANDAT_API_MAX_CONCURRENCY", "").strip()
    limit = int(raw) if raw.isdigit() and int(raw) > 0 else 64
    import importlib.util

    # uvloop и httptools — если установлены (extra api-fast): цикл событий и разбор HTTP на C, ответы те же.
    fast_http = importlib.util.find_spec("httptools") is not None
    options: dict[str, Any] = {
        "limit_concurrency": limit,
        "timeout_keep_alive": 5,
        "server_header": False,
        "proxy_headers": False,
        # TCP_NODELAY и при --workers N: иначе +40 мс на запрос по keep-alive (api/nodelay.py).
        "http": f"mandat.api.nodelay:NoDelay{'HttpTools' if fast_http else 'H11'}Protocol",
    }
    if importlib.util.find_spec("uvloop") is not None:
        options["loop"] = "uvloop"
    return options


def cmd_mcp(args: argparse.Namespace) -> int:
    """MCP по stdio: проверка кейса и допуск операции для ИИ-агентов банка (docs/MCP.md). В stdout — только MCP."""
    from mandat import mcp

    try:
        session = mcp.Session.from_env()
    except ValueError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    return mcp.serve(sys.stdin.buffer, sys.stdout.buffer, session)


def cmd_restore_check(args: argparse.Namespace) -> int:
    """После восстановления из копии: удалённое не воскресло, отзывы не откатились, журнал цел (services/recovery)."""
    from mandat.api.sealing import sealed
    from mandat.api.storage import open_store
    from mandat.services.recovery import check_restore

    store = sealed(open_store(args.store))
    if store.name == "memory":
        print("укажите восстановленное хранилище: --store sqlite:///… или redis://…", file=sys.stderr)
        return 2
    status_dir = Path(args.status_dir) if args.status_dir else None
    signer = None
    if args.repair and status_dir is not None:
        from mandat.audit.signing import SignerError, signer_from_env

        key_path = Path(args.key) if args.key else status_dir.parent / "keys" / "attestation_ed25519.pem"
        try:
            signer = signer_from_env(key_path, create=False)
        except SignerError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return 2
    report = check_restore(Path(args.journal), store, status_dir=status_dir, repair=args.repair, signer=signer)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    """Копия каталога данных без остановки службы и ротация (services/backup); код 1 — копия есть, но с замечаниями."""
    from mandat.services.backup import backup

    raw = args.data_dir or os.environ.get("MANDAT_STAND_DIR", "").strip()
    if not raw:
        print("ошибка: укажите --data-dir или MANDAT_STAND_DIR", file=sys.stderr)
        return 2
    result = backup(Path(raw), Path(args.out), keep=args.keep, exclude=tuple(args.exclude or ()))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    for problem in result.problems:
        print(f"замечание: {problem}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_incident_report(args: argparse.Namespace) -> int:
    """Сроки 24 и 72 часа от выявления и факты из журнала для расследования (services/incident)."""
    from mandat.services.incident import incident_report, parse_moment

    warnings: list[str] = []
    detected = parse_moment(args.detected_at, warnings, "--detected-at")
    since = parse_moment(args.since, warnings, "--since") if args.since else None
    until = parse_moment(args.until, warnings, "--until") if args.until else None
    report = incident_report(
        Path(args.journal), detected_at=detected, since=since, until=until, actor=args.actor, warnings=warnings
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.journal_ok else 1


def cmd_access_review(args: argparse.Namespace) -> int:
    """Ревизия учётных записей API без изменения списка; ``--strict`` — код 1, если есть что снять или назначить."""
    from datetime import UTC, datetime

    from mandat.api.principals import parse_users, review_access
    from mandat.strictjson import strict_loads

    text = (args.users if args.users is not None else os.environ.get("MANDAT_API_USERS", "")).strip()
    if not text:
        print("нет списка пользователей: --users или MANDAT_API_USERS", file=sys.stderr)
        return 2
    if not text.startswith("["):
        text = Path(text).read_text(encoding="utf-8")
    # Ревизия читает и список, который в pilot/prod не загрузился бы: её задача — найти такие записи.
    entries = parse_users(strict_loads(text), require_expiry=False)
    result = review_access(entries, now=datetime.now(UTC), warn_days=args.warn_days, long_days=args.long_days)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.strict and result["needs_action"] else 0


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
    from datetime import UTC, datetime, timedelta

    from mandat.api.principals import ROLES, hash_token
    from mandat.services.env_policy import protected

    if args.role not in ROLES:
        print(f"роль: одна из {', '.join(sorted(ROLES))}", file=sys.stderr)
        return 2
    audiences = list(dict.fromkeys(args.audience or []))
    if audiences and args.role != "dbo":
        print("--audience: только для роли dbo (учётная запись системы банка)", file=sys.stderr)
        return 2
    if args.role == "dbo" and not audiences and protected():
        print("в защищённом режиме у роли dbo задайте --audience", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    entry: dict[str, Any] = {"name": args.name, "role": args.role, "token_sha256": hash_token(token)}
    if audiences:
        entry["audiences"] = audiences
    if args.owner:
        entry["owner"] = args.owner
    if args.expires_in_days is not None:
        import math

        from mandat.api.principals import MAX_TOKEN_TTL_DAYS

        if not math.isfinite(args.expires_in_days) or not 0 < args.expires_in_days <= MAX_TOKEN_TTL_DAYS:
            print(f"--expires-in-days: число от 0 (не включая) до {MAX_TOKEN_TTL_DAYS}", file=sys.stderr)
            return 2
        entry["expires_at"] = (datetime.now(UTC) + timedelta(days=args.expires_in_days)).isoformat(timespec="seconds")
    elif protected():
        print("в защищённом режиме задайте --expires-in-days (expires_at обязателен)", file=sys.stderr)
        return 2
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
    m.add_argument("--as-of", help="момент проверки сроков МЧД, ISO 8601; без пояса — Москва")
    m.add_argument("--notice-days", type=int, default=NOTICE_DAYS, help="за сколько дней предупреждать о конце срока")
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

    lc = sub.add_parser("law-calendar", help="нормы пакета правил: что вступает в силу и что утрачивает её")
    lc.add_argument("--on", help="дата отсчёта ГГГГ-ММ-ДД (по умолчанию сегодня по Москве)")
    lc.add_argument("--days", type=int, default=365, help="окно вперёд, дней (365)")
    lc.set_defaults(func=cmd_law_calendar)

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

    av = sub.add_parser("approvals-verify", help="проверить подписанные записи утверждения правил")
    av.add_argument("--file", help="файл утверждений; по умолчанию MANDAT_APPROVALS или пустой файл пакета")
    av.add_argument("--keys", help="связка ключей утверждающих; по умолчанию MANDAT_APPROVAL_KEYS")
    av.add_argument("--at", help="момент проверки с часовым поясом; по умолчанию сейчас")
    av.set_defaults(func=cmd_approvals_verify)

    asg = sub.add_parser("approvals-sign", help="стенд: подписать утверждение правила ключом PEM")
    asg.add_argument("--rule", required=True, help="id правила, например R-TERM-002")
    asg.add_argument("--key", required=True, help="закрытый ключ Ed25519 утверждающего (PEM PKCS#8)")
    asg.add_argument("--role", required=True, help="роль утверждающего, без ФИО")
    asg.add_argument("--norm-edition", required=True, help="редакция нормы, по которой сверено правило")
    asg.add_argument("--checked-on", required=True, help="дата сверки нормы, ГГГГ-ММ-ДД")
    asg.add_argument("--valid-until", required=True, help="последний день действия записи, не дальше года")
    asg.add_argument("--file", help="добавить запись в этот файл утверждений; без него — вывести")
    asg.set_defaults(func=cmd_approvals_sign)

    sc = sub.add_parser("showcase", help="сценарии: захват до платежа и повторный клиент с реестром заключений")
    sc.add_argument("--out", default="var/showcase", help="каталог для журнала, списков статусов и отчёта сценария")
    sc.set_defaults(func=cmd_showcase)

    x = sub.add_parser("examples", help="пересобрать демо-кейсы")
    x.add_argument("--out", default="examples/cases")
    x.set_defaults(func=cmd_examples)

    u = sub.add_parser("api-user", help="выпустить токен пользователя API и его хеш")
    u.add_argument("--name", required=True)
    u.add_argument("--role", required=True, help="operator, reviewer, auditor, admin или dbo (система банка)")
    u.add_argument("--expires-in-days", type=float, default=None, help="срок жизни токена; в pilot/prod обязателен")
    u.add_argument(
        "--audience", action="append", help="получатель удостоверений для роли dbo; можно несколько, в pilot/prod нужен"
    )
    u.add_argument("--owner", default=None, help="кто отзовёт доступ, когда работа закончится (для access-review)")
    u.set_defaults(func=cmd_api_user)

    ar = sub.add_parser("access-review", help="ревизия учётных записей API: сроки, отзыв, владелец отзыва")
    ar.add_argument("--users", default=None, help="JSON-массив или путь к файлу; по умолчанию MANDAT_API_USERS")
    ar.add_argument("--warn-days", type=int, default=14, help="предупредить за столько дней до конца срока")
    ar.add_argument("--long-days", type=int, default=90, help="срок длиннее этого — пересмотреть")
    ar.add_argument("--strict", action="store_true", help="код 1, если у действующей записи нет срока или владельца")
    ar.set_defaults(func=cmd_access_review)

    rc = sub.add_parser("restore-check", help="после восстановления из копии: удаления, отзывы и журнал сверены")
    rc.add_argument("--journal", required=True, help="журнал аудита, который ведётся дальше (не из копии)")
    rc.add_argument("--store", default=None, help="восстановленное хранилище; по умолчанию MANDAT_API_STORE")
    rc.add_argument("--status-dir", default=None, help="восстановленный каталог списков статусов")
    rc.add_argument("--repair", action="store_true", help="повторить удаления и отзывы, записать это в журнал")
    rc.add_argument("--key", default=None, help="ключ подписи списков статусов для --repair (иначе MANDAT_SIGNER)")
    rc.set_defaults(func=cmd_restore_check)

    bk = sub.add_parser("backup", help="копия каталога данных без остановки службы: журнал, базы, файлы; ротация")
    bk.add_argument("--data-dir", default=None, help="каталог данных; по умолчанию MANDAT_STAND_DIR")
    bk.add_argument("--out", required=True, help="каталог копий, вне каталога данных")
    bk.add_argument("--keep", type=int, default=14, help="сколько последних копий оставить (14)")
    bk.add_argument(
        "--exclude", action="append", help="шаблон пути от каталога данных или имени, который не копируется"
    )
    bk.set_defaults(func=cmd_backup)

    ir = sub.add_parser("incident-report", help="инцидент с ПДн: сроки 24 и 72 ч от выявления и факты журнала")
    ir.add_argument("--journal", required=True)
    ir.add_argument("--detected-at", required=True, help="момент выявления, ISO 8601; без пояса — московское время")
    ir.add_argument("--since", default=None, help="начало окна расследования")
    ir.add_argument("--until", default=None, help="конец окна; по умолчанию момент выявления")
    ir.add_argument("--actor", default=None, help="только действия этой учётной записи")
    ir.set_defaults(func=cmd_incident_report)

    s = sub.add_parser("serve", help="запустить HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1, help="больше одного нужен MANDAT_API_STORE; 0 — по числу ядер")
    s.set_defaults(func=cmd_serve)

    mc = sub.add_parser("mcp", help="MCP по stdio: проверка полномочий и допуск операции для ИИ-агентов банка")
    mc.set_defaults(func=cmd_mcp)
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
