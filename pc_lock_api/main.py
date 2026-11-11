"""Ponto de entrada: arranca a API HTTP e o ecrã de bloqueio.

    python main.py                   # usa config.json ao lado deste ficheiro
    python main.py --generate-token  # gera um token seguro para o config.json
    python main.py --simulate        # não bloqueia nada, só regista (testes)
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from pclock.api import create_app
from pclock.config import ConfigError, generate_token, load_config
from pclock.controller import LockController, StateStore

APP_DIR = Path(__file__).resolve().parent


def setup_logging(log_file: Path) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # com pythonw.exe não há consola
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="API para bloquear/desbloquear este PC")
    parser.add_argument("--config", default=str(APP_DIR / "config.json"))
    parser.add_argument("--generate-token", action="store_true", help="gera um token e sai")
    parser.add_argument("--simulate", action="store_true",
                        help="não bloqueia o ecrã, apenas regista (para testes)")
    args = parser.parse_args()

    if args.generate_token:
        print(generate_token())
        return 0

    setup_logging(Path(args.config).resolve().parent / "pclock.log")
    log = logging.getLogger("pclock")

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if args.simulate or sys.platform != "win32":
        if not args.simulate:
            log.warning("Não é Windows: a correr em modo de simulação.")
        from pclock.console_backend import ConsoleBackend
        backend = ConsoleBackend()
    else:
        from pclock.win_lockscreen import WindowsLockScreen
        backend = WindowsLockScreen(
            unlock_pin=cfg.unlock_pin,
            policy_backup_file=Path(cfg.state_file).with_name("policies_backup.json"),
        )

    controller = LockController(backend, StateStore(cfg.state_file))
    backend.on_local_unlock = lambda: controller.unlock(source="pin")

    # Repor bloqueio pendente (reboot/crash) ou limpar restos de um bloqueio anterior.
    controller.restore()
    if not controller.status()["locked"]:
        backend.hide()

    app = create_app(controller, cfg.token, max_message_length=cfg.max_message_length)
    server = uvicorn.Server(uvicorn.Config(
        app,
        host=cfg.host,
        port=cfg.port,
        ssl_certfile=cfg.ssl_certfile,
        ssl_keyfile=cfg.ssl_keyfile,
        log_config=None,
        access_log=True,
    ))
    server_thread = threading.Thread(target=server.run, name="api", daemon=True)
    server_thread.start()

    # Se a API não arrancar (ex.: porta ocupada) não faz sentido continuar — mas nunca
    # largamos um bloqueio ativo: o PC continua bloqueado até ao PIN ou a um reinício
    # (no arranque seguinte a API volta a subir e o bloqueio é reposto).
    def watchdog() -> None:
        server_thread.join()
        if server.should_exit:
            return
        if controller.status()["locked"]:
            log.error("O servidor HTTP terminou inesperadamente; o PC mantém-se bloqueado.")
        else:
            log.error("O servidor HTTP terminou inesperadamente; a sair.")
            backend.shutdown()

    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()

    scheme = "https" if cfg.ssl_certfile else "http"
    log.info("PC Lock API a escutar em %s://%s:%d", scheme, cfg.host, cfg.port)
    try:
        backend.run()  # bloqueia a thread principal (ciclo do Tkinter no Windows)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        server_thread.join(5)
    return 0 if server.started else 1


if __name__ == "__main__":
    sys.exit(main())
