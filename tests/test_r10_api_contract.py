"""R4: контракт HTTP API и защита каждого маршрута (перенесено из KonkovDV/Kontur, scripts/check_contracts.py)."""

from __future__ import annotations

import importlib.util

from conftest import ROOT
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from mandat.api.app import create_app

PUBLIC = {("GET", "/health")}


def _export():
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_openapi_matches_app():
    module = _export()
    assert module.TARGET.read_text(encoding="utf-8") == module.render(), "python scripts/export_openapi.py"


def _routes(app):
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                yield method, route.path


def test_every_route_except_health_requires_token():
    """Новый маршрут без ``Depends(auth)`` или ``writer`` ломает этот тест, а не попадает в прод открытым."""
    app = create_app(token="secret")
    client = TestClient(app)
    routes = set(_routes(app))
    assert PUBLIC <= routes and len(routes) > len(PUBLIC)
    for method, path in sorted(routes - PUBLIC):
        url = path.replace("{case_id}", "c-1")
        response = client.request(method, url, json={})
        assert response.status_code == 401, (method, path, response.status_code)
        bad = client.request(method, url, json={}, headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == 401, (method, path, bad.status_code)
    assert client.get("/health").status_code == 200


def test_read_only_role_cannot_call_writing_routes():
    import hashlib
    import json

    from mandat.api.principals import ROLES

    reader = next(role for role, rights in ROLES.items() if not rights["write"])
    digest = hashlib.sha256(("a" * 32).encode()).hexdigest()
    app = create_app(users=json.dumps([{"name": "r", "role": reader, "token_sha256": digest}]))
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + "a" * 32}
    for method, path in sorted(set(_routes(app)) - PUBLIC):
        if method == "POST":
            assert client.request(method, path, json={}, headers=headers).status_code == 403, path
