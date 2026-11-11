import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pclock.api import AuthFailureLimiter, create_app  # noqa: E402
from pclock.config import ConfigError, load_config  # noqa: E402
from pclock.controller import LockController, StateStore  # noqa: E402

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBackend:
    def __init__(self):
        self.calls = []

    def show(self, message):
        self.calls.append(("show", message))

    def hide(self):
        self.calls.append(("hide",))


@pytest.fixture
def env(tmp_path):
    backend = FakeBackend()
    store = StateStore(tmp_path / "state.json")
    controller = LockController(backend, store)
    app = create_app(controller, TOKEN, max_message_length=50,
                     limiter=AuthFailureLimiter(max_failures=3, window_seconds=60))
    return TestClient(app), backend, store


@pytest.mark.parametrize("method,path", [("post", "/lock"), ("post", "/unlock"), ("get", "/status")])
def test_requires_auth(env, method, path):
    client, backend, _ = env
    r = client.request(method, path, json={"message": "x"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    r = client.request(method, path, json={"message": "x"},
                       headers={"Authorization": "Bearer errado"})
    assert r.status_code == 401
    assert backend.calls == []


def test_lock_and_unlock(env):
    client, backend, store = env
    r = client.post("/lock", json={"message": "  Hora de estudar!  "}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is True and body["message"] == "Hora de estudar!"
    assert body["locked_at"]
    assert backend.calls == [("show", "Hora de estudar!")]
    assert store.load()["locked"] is True

    assert client.get("/status", headers=AUTH).json()["locked"] is True

    r = client.post("/unlock", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"locked": False, "message": None, "locked_at": None}
    assert backend.calls[-1] == ("hide",)
    assert store.load()["locked"] is False


def test_lock_again_updates_message_keeps_timestamp(env):
    client, _, _ = env
    first = client.post("/lock", json={"message": "a"}, headers=AUTH).json()
    second = client.post("/lock", json={"message": "b"}, headers=AUTH).json()
    assert second["message"] == "b"
    assert second["locked_at"] == first["locked_at"]


@pytest.mark.parametrize("payload", [{}, {"message": "   "}, {"message": "x" * 51}])
def test_lock_validation(env, payload):
    client, backend, _ = env
    assert client.post("/lock", json=payload, headers=AUTH).status_code == 422
    assert backend.calls == []


def test_brute_force_is_throttled(env):
    client, _, _ = env
    bad = {"Authorization": "Bearer errado"}
    for _ in range(3):
        assert client.get("/status", headers=bad).status_code == 401
    # mesmo com o token certo, o IP fica temporariamente bloqueado
    assert client.get("/status", headers=AUTH).status_code == 429


def test_no_public_docs(env):
    client, _, _ = env
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404


def test_restore_relocks_after_restart(tmp_path):
    store = StateStore(tmp_path / "state.json")
    LockController(FakeBackend(), store).lock("Volto já")

    backend = FakeBackend()
    controller = LockController(backend, store)
    controller.restore()
    assert controller.status()["locked"] is True
    assert backend.calls == [("show", "Volto já")]


def test_config_validation(tmp_path, monkeypatch):
    for var in ("PCLOCK_TOKEN", "PCLOCK_PORT", "PCLOCK_UNLOCK_PIN"):
        monkeypatch.delenv(var, raising=False)
    cfg_file = tmp_path / "config.json"

    cfg_file.write_text(json.dumps({"token": "curto"}))
    with pytest.raises(ConfigError):
        load_config(cfg_file)

    # BOM (PowerShell 5.1) + override por variável de ambiente
    cfg_file.write_text(json.dumps({"token": TOKEN, "unlock_pin": "1234"}), encoding="utf-8-sig")
    monkeypatch.setenv("PCLOCK_PORT", "9000")
    cfg = load_config(cfg_file)
    assert cfg.port == 9000 and cfg.unlock_pin == "1234"
    assert Path(cfg.state_file) == tmp_path / "state.json"

    cfg_file.write_text(json.dumps({"token": TOKEN, "ssl_certfile": "c.pem"}))
    with pytest.raises(ConfigError):
        load_config(cfg_file)
