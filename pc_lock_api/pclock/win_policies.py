"""Políticas do Windows (registo HKCU) aplicadas durante o bloqueio.

Ctrl+Alt+Del não pode ser intercetado, mas podemos esvaziar o ecrã que ele abre:
sem Gestor de Tarefas, sem "Bloquear", "Terminar sessão", "Alterar palavra-passe" e
sem opções de encerrar. Ao carregar em "Cancelar" o utilizador volta ao ecrã de bloqueio.

Os valores originais são guardados num ficheiro de backup antes de serem alterados, para
serem repostos no desbloqueio — mesmo que o processo tenha morrido entretanto.
"""

from __future__ import annotations

import json
import logging
import os
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

SYSTEM = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"

POLICIES: list[tuple[str, str]] = [
    (SYSTEM, "DisableTaskMgr"),
    (SYSTEM, "DisableLockWorkstation"),
    (SYSTEM, "DisableChangePassword"),
    (EXPLORER, "NoLogoff"),
    (EXPLORER, "NoClose"),
]


def _read(subkey: str, name: str) -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as k:
            value, _ = winreg.QueryValueEx(k, name)
            return int(value)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _write(subkey: str, name: str, value: int | None) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


class PolicyGuard:
    def __init__(self, backup_file: str | Path):
        self.backup_file = Path(backup_file)

    def apply(self) -> None:
        # Se já existe backup (ex.: crash durante um bloqueio) não o reescrevemos,
        # senão guardaríamos os nossos próprios valores como "originais".
        if not self.backup_file.exists():
            backup = {f"{s}|{n}": _read(s, n) for s, n in POLICIES}
            self.backup_file.write_text(json.dumps(backup), encoding="utf-8")
        for subkey, name in POLICIES:
            try:
                _write(subkey, name, 1)
            except OSError:
                log.exception("Não foi possível aplicar a política %s", name)

    def restore(self) -> None:
        if not self.backup_file.exists():
            return
        try:
            backup = json.loads(self.backup_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = {}
        for subkey, name in POLICIES:
            try:
                _write(subkey, name, backup.get(f"{subkey}|{name}"))
            except OSError:
                log.exception("Não foi possível repor a política %s", name)
        os.remove(self.backup_file)
