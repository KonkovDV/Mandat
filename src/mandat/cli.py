"""Командная строка «Мандата».

mandat verify examples/cases/01_ok --html var/01.html
mandat demo                      # все демо-кейсы: отчёты, журнал, удостоверения, списки статусов в var/demo
mandat monitor examples/events/takeover.json --status-dir var/demo/status
mandat attest-verify var/demo/attestations/01_ok.json --status-dir var/demo/status
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
    key = attestation.load_or_create_key(out / "keys" / "attestation_ed25519.pem")
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
        cred = attestation.issue(report, key, status=status.credential_status(index))
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
    result: dict = {"alerts": [a.as_dict() for a in alerts]}
    if args.status_dir:
        status_dir = Path(args.status_dir)
        key_path = Path(args.key) if args.key else status_dir.parent / "keys" / "attestation_ed25519.pem"
        if not key_path.exists() or not (status_dir / "registry.json").exists():
            print("нет списков статусов или ключа: сначала выполните mandat demo", file=sys.stderr)
            return 2
        key = attestation.load_or_create_key(key_path)
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


def cmd_attest_verify(args: argparse.Namespace) -> int:
    cred = json.loads(Path(args.path).read_text(encoding="utf-8"))
    method = cred.get("proof", {}).get("verificationMethod", "")
    key = attestation.public_key_from_multibase(args.public_key or method.split("#", 1)[-1])
    ok = attestation.verify(cred, key)
    print("подпись верна" if ok else "подпись НЕ верна")
    if ok and not args.public_key:
        print("ключ взят из самого удостоверения: чтобы проверить издателя, передайте --public-key из public_key.txt")
    if ok and args.status_dir:
        try:
            status = check_status(cred, Path(args.status_dir), key)
        except (OSError, ValueError, KeyError) as exc:
            print(f"статус не проверен: {exc}", file=sys.stderr)
            return 1
        print(f"статус удостоверения: {status['state']}")
        return 0 if status["state"] == "действует" else 3
    return 0 if ok else 1


def cmd_examples(args: argparse.Namespace) -> int:
    from mandat.examples import write_examples

    for path in write_examples(Path(args.out)):
        print(path)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from mandat.api.principals import access_problem

    problem = access_problem()
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
    if args.workers > 1:
        uvicorn.run("mandat.api.app:create_app", factory=True, host=args.host, port=args.port, workers=args.workers)
    else:
        from mandat.api.app import create_app

        uvicorn.run(create_app(store=store), host=args.host, port=args.port)
    return 0


def cmd_webhook_secret(args: argparse.Namespace) -> int:
    from mandat.services.webhooks import new_secret

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
    v.add_argument("--profile", default="demo", choices=["demo", "strict"])
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

    a = sub.add_parser("attest-verify", help="проверить подпись удостоверения")
    a.add_argument("path")
    a.add_argument("--public-key")
    a.add_argument("--status-dir", help="каталог списков статусов W3C Bitstring Status List")
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
    w = sub.add_parser("webhook-secret", help="новый секрет вебхука в формате whsec_")
    w.set_defaults(func=cmd_webhook_secret)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except journal.JournalKeyMissing as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
