"""Backend de simulação: em vez de bloquear, escreve no log.

Útil para testar a API fora do Windows (`python main.py --simulate`).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)


class ConsoleBackend:
    def __init__(self) -> None:
        self.on_local_unlock: Callable[[], None] | None = None
        self._stop = threading.Event()

    def show(self, message: str) -> None:
        log.info("[SIMULAÇÃO] ecrã de bloqueio visível: %s", message)

    def hide(self) -> None:
        log.info("[SIMULAÇÃO] ecrã de bloqueio escondido")

    def run(self) -> None:
        while not self._stop.wait(0.5):
            pass

    def shutdown(self) -> None:
        self._stop.set()
