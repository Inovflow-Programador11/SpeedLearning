"""Hook global de teclado (WH_KEYBOARD_LL) que bloqueia atalhos do Windows.

Enquanto ativo, só deixa passar as teclas da lista branca (letras, números, Backspace,
Enter, Shift) — usadas para escrever o PIN de desbloqueio local, se estiver configurado.
Tudo o resto (tecla Windows, Alt+Tab, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc, ...) é engolido.
Como Ctrl e Alt nunca chegam ao sistema, nenhuma combinação com eles funciona.

Nota: Ctrl+Alt+Del (Secure Attention Sequence) é tratado pelo kernel/winlogon e não pode
ser intercetado por nenhum programa; isso é mitigado pelas políticas em win_policies.py.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes as wt

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_USER = 0x0400
PM_NOREMOVE = 0x0000

VK_BACK, VK_RETURN = 0x08, 0x0D
VK_SHIFT, VK_LSHIFT, VK_RSHIFT = 0x10, 0xA0, 0xA1
VK_LWIN, VK_RWIN = 0x5B, 0x5C

PIN_KEYS = (
    set(range(0x30, 0x3A))  # 0-9
    | set(range(0x41, 0x5B))  # A-Z
    | set(range(0x60, 0x6A))  # teclado numérico 0-9
    | {VK_BACK, VK_RETURN, VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


class KeyboardBlocker:
    def __init__(self, allow_pin_keys: bool):
        self._allowed = PIN_KEYS if allow_pin_keys else set()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._proc = HOOKPROC(self._callback)  # manter referência viva!

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready,), name="keyboard-hook", daemon=True
        )
        self._thread.start()
        ready.wait(3)

    def stop(self) -> None:
        if not self.active:
            return
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(3)
        self._thread = None

    # O hook LL tem de ser instalado numa thread com ciclo de mensagens próprio.
    def _run(self, ready: threading.Event) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        # força a criação da fila de mensagens desta thread (para receber WM_QUIT)
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        ready.set()
        if not hook:
            log.error("SetWindowsHookExW falhou (erro %s)", ctypes.get_last_error())
            return
        log.info("Hook de teclado ativo")
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            log.info("Hook de teclado removido")

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            if vk in (VK_LWIN, VK_RWIN):
                return 1
            is_up = w_param in (WM_KEYUP, WM_SYSKEYUP)
            # Deixa passar key-ups (evita teclas "presas") e as teclas permitidas.
            if not is_up and vk not in self._allowed:
                return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)
