"""Estado do bloqueio (thread-safe e persistido em disco).

O estado é guardado num ficheiro JSON para que, se o PC for reiniciado ou o processo
morrer enquanto está bloqueado, o bloqueio seja reposto assim que a aplicação arrancar.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class LockBackend(Protocol):
    """Mecanismo que efetivamente bloqueia/desbloqueia o ecrã."""

    def show(self, message: str) -> None: ...

    def hide(self) -> None: ...


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.exception("Não foi possível ler o estado em %s", self.path)
            return {}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class LockController:
    def __init__(self, backend: LockBackend, store: StateStore):
        self._backend = backend
        self._store = store
        self._mutex = threading.Lock()
        self._locked = False
        self._message: str | None = None
        self._locked_at: str | None = None

    # -- API pública -----------------------------------------------------------------
    def lock(self, message: str) -> dict:
        with self._mutex:
            if not self._locked:
                self._locked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._locked = True
            self._message = message
            self._persist()
            self._backend.show(message)
            log.info("PC bloqueado. Mensagem: %r", message)
            return self._status()

    def unlock(self, source: str = "api") -> dict:
        with self._mutex:
            was_locked = self._locked
            self._locked = False
            self._message = None
            self._locked_at = None
            self._persist()
            self._backend.hide()
            if was_locked:
                log.info("PC desbloqueado (origem: %s).", source)
            return self._status()

    def status(self) -> dict:
        with self._mutex:
            return self._status()

    def restore(self) -> None:
        """Repõe o bloqueio guardado (chamado no arranque)."""
        state = self._store.load()
        if state.get("locked"):
            log.warning("Estado anterior estava bloqueado: a repor o bloqueio.")
            with self._mutex:
                self._locked = True
                self._message = state.get("message") or ""
                self._locked_at = state.get("locked_at")
                self._backend.show(self._message)

    # -- internos --------------------------------------------------------------------
    def _status(self) -> dict:
        return {"locked": self._locked, "message": self._message, "locked_at": self._locked_at}

    def _persist(self) -> None:
        try:
            self._store.save(self._status())
        except OSError:
            log.exception("Falha ao guardar o estado do bloqueio")
