"""mandat serve не стартует без доступа: ни токена, ни пользователей."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandat import cli
from mandat.api.principals import access_problem, hash_token
from mandat.services.env_policy import key_problem


def test_serve_refuses_without_token_and_users(monkeypatch, capsys):
    monkeypatch.delenv("MANDAT_API_TOKEN", raising=False)
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    assert cli.main(["serve"]) == 2
    assert "MANDAT_API_USERS" in capsys.readouterr().err


def test_serve_refuses_broken_users(monkeypatch):
    monkeypatch.delenv("MANDAT_API_TOKEN", raising=False)
    monkeypatch.setenv("MANDAT_API_USERS", '[{"name": "a"}]')
    assert "не читается" in (access_problem() or "")


def test_either_token_or_users_is_enough(monkeypatch):
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", "secret")
    assert access_problem() is None
    monkeypatch.delenv("MANDAT_API_TOKEN")
    users = [{"name": "a", "role": "admin", "token_sha256": hash_token("t")}]
    monkeypatch.setenv("MANDAT_API_USERS", json.dumps(users))
    assert access_problem() is None


def test_compose_does_not_demand_only_the_token(root):
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MANDAT_API_TOKEN:?" not in compose
    assert "MANDAT_API_USERS" in compose


STRONG = "k" * 32


def _access(monkeypatch):
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", STRONG)


@pytest.mark.parametrize("env", ["pilot", "prod", "unknown"])
@pytest.mark.parametrize(
    ("journal", "pii", "missing"),
    [
        (None, None, ("MANDAT_JOURNAL_KEY", "MANDAT_PII_KEY")),
        (STRONG, None, ("MANDAT_PII_KEY",)),
        (None, STRONG, ("MANDAT_JOURNAL_KEY",)),
        ("short", STRONG, ("MANDAT_JOURNAL_KEY",)),
    ],
)
def test_serve_refuses_protected_mode_without_keys(monkeypatch, capsys, env, journal, pii, missing):
    _access(monkeypatch)
    monkeypatch.setenv("MANDAT_ENV", env)
    for name, value in (("MANDAT_JOURNAL_KEY", journal), ("MANDAT_PII_KEY", pii)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    for name in missing:
        assert name in err


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_key_check_passes_with_keys(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", STRONG)
    monkeypatch.setenv("MANDAT_PII_KEY", STRONG)
    assert key_problem() is None


@pytest.mark.parametrize("env", ["demo", "dev", "test"])
def test_key_check_skipped_in_keyless_modes(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_PII_KEY", raising=False)
    assert key_problem() is None


def test_compose_requires_explicit_env(root):
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MANDAT_ENV:?" in compose
    assert "MANDAT_ENV:-demo" not in compose


def test_compose_follows_cis_limits(root):
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    for needle in ("cap_drop:", "- ALL", "pids_limit:", "mem_limit:", "read_only: true", "no-new-privileges:true"):
        assert needle in compose


def test_server_options_bound_concurrency(monkeypatch):
    monkeypatch.delenv("MANDAT_API_MAX_CONCURRENCY", raising=False)
    options = cli.server_options()
    assert options["limit_concurrency"] == 64
    assert options["server_header"] is False and options["proxy_headers"] is False
    monkeypatch.setenv("MANDAT_API_MAX_CONCURRENCY", "16")
    assert cli.server_options()["limit_concurrency"] == 16
    monkeypatch.setenv("MANDAT_API_MAX_CONCURRENCY", "-1")
    assert cli.server_options()["limit_concurrency"] == 64


def test_build_backend_is_hash_pinned():
    """R2-SC-1: setuptools из lock-файла с хешами, сборка без изоляции — pip ничего не скачивает сам."""
    root = Path(__file__).resolve().parents[1]
    docker = (root / "Dockerfile").read_text(encoding="utf-8")
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lock = (root / "requirements" / "build.lock").read_text(encoding="utf-8")
    assert "-r requirements/build.lock" in docker and "--no-build-isolation ." in docker
    assert "-r requirements/build.lock" in ci and "--no-build-isolation -e ." in ci
    assert "setuptools==" in lock and "--hash=sha256:" in lock


def test_every_pip_install_is_hash_pinned():
    """R3: pip и инструменты CI — тоже из lock-файлов с хешами; незакреплённых установок нет."""
    import re

    root = Path(__file__).resolve().parents[1]
    files = [".github/workflows/ci.yml", "Dockerfile", "run-demo.sh", "run-demo.ps1", "Makefile"]
    for name in files:
        text = (root / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"pip(\.exe)?\s+install|-m pip install", line) and not line.lstrip().startswith("#"):
                assert "--require-hashes" in line or "--no-deps" in line, f"{name}: {line.strip()}"
        if name != "Makefile":
            assert "pip.lock" in text or name == "Dockerfile" and "pip.lock" in text, name
    for lock in ("pip.lock", "tools.lock", "build.lock"):
        assert "--hash=sha256:" in (root / "requirements" / lock).read_text(encoding="utf-8")


def test_runtime_image_drops_pip():
    """CI docker/Trivy: вендоренные в pip urllib3/msgpack/pkg_resources не попадают в образ."""
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "pip uninstall -y setuptools pip" in docker
