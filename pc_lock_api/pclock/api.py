"""Endpoints HTTP (FastAPI) com autenticação por token Bearer."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from .controller import LockController

log = logging.getLogger(__name__)


class AuthFailureLimiter:
    """Bloqueia temporariamente um IP após demasiadas tentativas falhadas (anti brute-force)."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._mutex = threading.Lock()

    def _prune(self, ip: str, now: float) -> deque[float]:
        q = self._failures[ip]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def is_blocked(self, ip: str) -> bool:
        with self._mutex:
            return len(self._prune(ip, time.monotonic())) >= self.max_failures

    def record_failure(self, ip: str) -> None:
        with self._mutex:
            now = time.monotonic()
            self._prune(ip, now).append(now)

    def reset(self, ip: str) -> None:
        with self._mutex:
            self._failures.pop(ip, None)


class LockRequest(BaseModel):
    message: str = Field(..., description="Mensagem a mostrar no ecrã de bloqueio")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A mensagem não pode estar vazia")
        return v


class LockStatus(BaseModel):
    locked: bool
    message: str | None
    locked_at: str | None


def create_app(
    controller: LockController,
    token: str,
    *,
    max_message_length: int = 500,
    limiter: AuthFailureLimiter | None = None,
) -> FastAPI:
    limiter = limiter or AuthFailureLimiter()
    expected = token.encode("utf-8")
    bearer = HTTPBearer(auto_error=False)

    # Sem /docs nem /openapi.json: não expomos a descrição da API a quem não tem token.
    app = FastAPI(title="PC Lock API", docs_url=None, redoc_url=None, openapi_url=None)

    def require_auth(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        ip = request.client.host if request.client else "?"
        if limiter.is_blocked(ip):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiadas tentativas falhadas. Tente mais tarde.",
            )
        ok = (
            creds is not None
            and creds.scheme.lower() == "bearer"
            and hmac.compare_digest(creds.credentials.encode("utf-8"), expected)
        )
        if not ok:
            limiter.record_failure(ip)
            log.warning("Pedido não autorizado de %s para %s", ip, request.url.path)
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Não autorizado",
                headers={"WWW-Authenticate": "Bearer"},
            )
        limiter.reset(ip)

    auth = [Depends(require_auth)]

    @app.post("/lock", response_model=LockStatus, dependencies=auth)
    def lock(body: LockRequest) -> dict:
        """Bloqueia o PC e mostra `message` no ecrã de bloqueio.

        Se já estiver bloqueado, apenas atualiza a mensagem.
        """
        if len(body.message) > max_message_length:
            raise HTTPException(
                422,
                f"A mensagem não pode ter mais de {max_message_length} caracteres",
            )
        return controller.lock(body.message)

    @app.post("/unlock", response_model=LockStatus, dependencies=auth)
    def unlock() -> dict:
        """Desbloqueia o PC (idempotente)."""
        return controller.unlock(source="api")

    @app.get("/status", response_model=LockStatus, dependencies=auth)
    def get_status() -> dict:
        return controller.status()

    return app
