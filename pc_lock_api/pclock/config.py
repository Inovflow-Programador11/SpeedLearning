"""Carregamento da configuração (config.json + variáveis de ambiente)."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

MIN_TOKEN_LENGTH = 24


class ConfigError(Exception):
    pass


@dataclass
class Config:
    token: str
    host: str = "0.0.0.0"
    port: int = 8765
    unlock_pin: str | None = None
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    state_file: str = "state.json"
    max_message_length: int = 500


ENV_MAP = {
    "PCLOCK_TOKEN": "token",
    "PCLOCK_HOST": "host",
    "PCLOCK_PORT": "port",
    "PCLOCK_UNLOCK_PIN": "unlock_pin",
    "PCLOCK_SSL_CERTFILE": "ssl_certfile",
    "PCLOCK_SSL_KEYFILE": "ssl_keyfile",
    "PCLOCK_STATE_FILE": "state_file",
}


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def load_config(path: str | Path | None) -> Config:
    """Lê o config.json (se existir) e aplica as variáveis de ambiente por cima."""
    data: dict = {}
    base_dir = Path.cwd()
    if path:
        path = Path(path)
        base_dir = path.resolve().parent
        if path.exists():
            try:
                # utf-8-sig: o PowerShell 5.1 grava UTF-8 com BOM
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise ConfigError(f"config.json inválido: {exc}") from exc

    for env, key in ENV_MAP.items():
        if os.environ.get(env):
            data[key] = os.environ[env]

    token = (data.get("token") or "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"Defina um token com pelo menos {MIN_TOKEN_LENGTH} caracteres em config.json "
            "ou na variável PCLOCK_TOKEN (gere um com: python main.py --generate-token)."
        )

    pin = data.get("unlock_pin")
    pin = str(pin).strip() if pin not in (None, "") else None
    if pin is not None and (not pin.isalnum() or len(pin) < 4):
        raise ConfigError("unlock_pin tem de ter pelo menos 4 caracteres alfanuméricos.")

    cert, key = data.get("ssl_certfile") or None, data.get("ssl_keyfile") or None
    if bool(cert) != bool(key):
        raise ConfigError("Para HTTPS defina ssl_certfile E ssl_keyfile.")

    state_file = Path(data.get("state_file") or "state.json")
    if not state_file.is_absolute():
        state_file = base_dir / state_file

    try:
        port = int(data.get("port", 8765))
        max_len = int(data.get("max_message_length", 500))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Valor numérico inválido: {exc}") from exc

    return Config(
        token=token,
        host=str(data.get("host") or "0.0.0.0"),
        port=port,
        unlock_pin=pin,
        ssl_certfile=cert,
        ssl_keyfile=key,
        state_file=str(state_file),
        max_message_length=max_len,
    )
