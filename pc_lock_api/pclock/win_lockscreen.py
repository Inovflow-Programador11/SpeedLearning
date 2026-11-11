"""Ecrã de bloqueio para Windows.

- Uma janela Tk sem moldura, sempre no topo, a cobrir CADA monitor.
- Hook de teclado global que engole os atalhos do sistema (ver win_keyboard.py).
- Políticas do registo que desativam Gestor de Tarefas, terminar sessão, etc. (win_policies.py).
- A cada 300 ms as janelas são reposicionadas no topo e voltam a receber o foco.
- Opcional: PIN de desbloqueio local (para quando a rede falha).

O Tkinter tem de correr na thread principal; os pedidos vindos da API (outra thread)
chegam por uma fila que é lida periodicamente com `root.after`.
"""

from __future__ import annotations

import ctypes
import hmac
import logging
import queue
import time
import tkinter as tk
from ctypes import wintypes as wt
from datetime import datetime
from pathlib import Path
from typing import Callable

from .win_keyboard import KeyboardBlocker
from .win_policies import PolicyGuard

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

BG = "#0b1220"
FG = "#f8fafc"
MUTED = "#94a3b8"
ACCENT = "#ef4444"
FONT = "Segoe UI"

POLL_MS = 100
KEEP_ON_TOP_MS = 300
MAX_PIN_FAILURES = 5
PIN_COOLDOWN_SECONDS = 60
MONITORINFOF_PRIMARY = 1


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
)
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), MONITORENUMPROC, wt.LPARAM]
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]


def enable_dpi_awareness() -> None:
    """Coordenadas em píxeis reais (necessário para cobrir bem ecrãs com escala ≠ 100%)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def list_monitors() -> list[tuple[int, int, int, int, bool]]:
    """Devolve [(x, y, largura, altura, é_principal), ...]."""
    monitors: list[tuple[int, int, int, int, bool]] = []

    def _cb(hmon, _hdc, _rect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(
                (r.left, r.top, r.right - r.left, r.bottom - r.top,
                 bool(info.dwFlags & MONITORINFOF_PRIMARY))
            )
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    if not monitors:  # fallback: ecrã virtual inteiro
        x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        w, h = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        monitors.append((x, y, w, h, True))
    return monitors


class WindowsLockScreen:
    def __init__(self, unlock_pin: str | None, policy_backup_file: str | Path):
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.on_local_unlock: Callable[[], None] | None = None

        self._queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._pin = unlock_pin
        self._keyboard = KeyboardBlocker(allow_pin_keys=bool(unlock_pin))
        self._policies = PolicyGuard(policy_backup_file)

        self._windows: list[tk.Toplevel] = []
        self._message_var = tk.StringVar(master=self.root)
        self._since_var = tk.StringVar(master=self.root)
        self._pin_var = tk.StringVar(master=self.root)
        self._pin_feedback = tk.StringVar(master=self.root)
        self._pin_entry: tk.Entry | None = None
        self._pin_failures = 0
        self._pin_blocked_until = 0.0

    # -- chamado a partir de qualquer thread -----------------------------------------
    def show(self, message: str) -> None:
        self._queue.put(("show", message))

    def hide(self) -> None:
        self._queue.put(("hide", None))

    def shutdown(self) -> None:
        self._queue.put(("quit", None))

    # -- thread principal ------------------------------------------------------------
    def run(self) -> None:
        self.root.after(POLL_MS, self._poll)
        self.root.mainloop()

    def _poll(self) -> None:
        try:
            while True:
                action, arg = self._queue.get_nowait()
                if action == "show":
                    self._do_show(arg or "")
                elif action == "hide":
                    self._do_hide()
                elif action == "quit":
                    self._do_hide()
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        except Exception:  # nunca deixar o ciclo morrer
            log.exception("Erro ao processar pedido do ecrã de bloqueio")
        self.root.after(POLL_MS, self._poll)

    def _do_show(self, message: str) -> None:
        self._message_var.set(message)
        if self._windows:  # já bloqueado: só atualiza a mensagem
            return
        self._since_var.set("Bloqueado desde " + datetime.now().strftime("%d/%m/%Y %H:%M"))
        self._policies.apply()
        self._keyboard.start()
        for x, y, w, h, primary in list_monitors():
            self._windows.append(self._build_window(x, y, w, h, primary))
        self._keep_on_top()

    def _do_hide(self) -> None:
        self._keyboard.stop()
        for w in self._windows:
            w.destroy()
        self._windows.clear()
        self._pin_entry = None
        self._pin_var.set("")
        self._pin_feedback.set("")
        self._policies.restore()

    def _build_window(self, x: int, y: int, w: int, h: int, primary: bool) -> tk.Toplevel:
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)  # sem barra de título / botões
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        box = tk.Frame(win, bg=BG)
        box.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(box, text="🔒", font=(FONT, 64), bg=BG, fg=ACCENT).pack()
        tk.Label(box, text="Este computador está bloqueado", font=(FONT, 34, "bold"),
                 bg=BG, fg=FG).pack(pady=(10, 24))
        tk.Label(box, textvariable=self._message_var, font=(FONT, 22), bg=BG, fg=FG,
                 wraplength=int(w * 0.7), justify="center").pack(pady=(0, 24))
        tk.Label(box, textvariable=self._since_var, font=(FONT, 12), bg=BG, fg=MUTED).pack()

        if primary and self._pin:
            tk.Label(box, text="PIN de desbloqueio", font=(FONT, 12), bg=BG,
                     fg=MUTED).pack(pady=(48, 6))
            entry = tk.Entry(box, textvariable=self._pin_var, show="•", justify="center",
                             font=(FONT, 20), width=12, relief="flat",
                             bg="#1e293b", fg=FG, insertbackground=FG)
            entry.pack(ipady=6)
            entry.bind("<Return>", self._check_pin)
            tk.Label(box, textvariable=self._pin_feedback, font=(FONT, 12), bg=BG,
                     fg=ACCENT).pack(pady=(8, 0))
            self._pin_entry = entry
        return win

    def _keep_on_top(self) -> None:
        if not self._windows:
            return
        for w in self._windows:
            w.attributes("-topmost", True)
            w.lift()
        target = self._pin_entry or self._windows[0]
        target.focus_force()
        self.root.after(KEEP_ON_TOP_MS, self._keep_on_top)

    def _check_pin(self, _event=None) -> None:
        typed = self._pin_var.get().strip()
        self._pin_var.set("")
        remaining = self._pin_blocked_until - time.monotonic()
        if remaining > 0:
            self._pin_feedback.set(f"Demasiadas tentativas. Aguarde {int(remaining) + 1}s.")
            return
        if self._pin and hmac.compare_digest(typed.lower().encode(), self._pin.lower().encode()):
            log.info("Desbloqueio local com PIN")
            self._pin_failures = 0
            if self.on_local_unlock:
                self.on_local_unlock()
            return
        self._pin_failures += 1
        log.warning("PIN errado (%d)", self._pin_failures)
        if self._pin_failures >= MAX_PIN_FAILURES:
            self._pin_failures = 0
            self._pin_blocked_until = time.monotonic() + PIN_COOLDOWN_SECONDS
            self._pin_feedback.set(f"Demasiadas tentativas. Aguarde {PIN_COOLDOWN_SECONDS}s.")
        else:
            self._pin_feedback.set("PIN incorreto")
