# -*- coding: utf-8 -*-
"""
RU/EN Switcher v4
  • Левый  Ctrl (одиночный тап <= 300 мс) → Русский
  • Правый Ctrl (одиночный тап <= 300 мс) → Английский
  • Автосинхронизация иконки с реальной раскладкой активного окна Windows
  • Защита от повторного запуска (Single Instance Mutex)
Иконка в системном трее. Без консоли.
"""

import atexit
import ctypes
import math
import queue
import threading
from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw

# ─── WIN32 API И КОНСТАНТЫ ───────────────────────────────────────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_INPUTLANGCHANGEREQUEST = 0x0050
WM_QUIT = 0x0012
WM_TIMER = 0x0113

KLF_ACTIVATE = 0x00000001
LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
WH_KEYBOARD_LL = 13

ERROR_ALREADY_EXISTS = 183
TAP_TIMEOUT_MS = 300       # Максимальная длительность одиночного тапа (мс)
SYNC_TIMER_ID = 1          # ID таймера синхронизации раскладки
SYNC_INTERVAL_MS = 400     # Интервал проверки активной раскладки (мс)

# Строгая 64-битная типизация Win32 функций
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,      # idHook
    ctypes.c_void_p,   # lpfn (HOOKPROC)
    ctypes.c_void_p,   # hmod (NULL для WH_KEYBOARD_LL)
    wintypes.DWORD,    # dwThreadId (0 = все потоки)
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []

user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
]

user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]

user32.GetKeyboardLayoutList.restype = ctypes.c_int
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.c_void_p]

user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]

user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_ssize_t,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]

user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.TranslateMessage.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

user32.SetTimer.restype = ctypes.c_size_t
user32.SetTimer.argtypes = [
    wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p,
]
user32.KillTimer.restype = wintypes.BOOL
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]

user32.MessageBoxW.restype = ctypes.c_int
user32.MessageBoxW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
]

kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.argtypes = []
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetLastError.restype = wintypes.DWORD
kernel32.GetLastError.argtypes = []

HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      wintypes.DWORD),
        ("scanCode",    wintypes.DWORD),
        ("flags",       wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",        wintypes.DWORD),
        ("flags",         wintypes.DWORD),
        ("hwndActive",    wintypes.HWND),
        ("hwndFocus",     wintypes.HWND),
        ("hwndCapture",   wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize",  wintypes.HWND),
        ("hwndCaret",     wintypes.HWND),
        ("rcCaret",       wintypes.RECT),
    ]


user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetGUIThreadInfo.argtypes = [
    wintypes.DWORD, ctypes.POINTER(GUITHREADINFO),
]


# ─── ПОИСК И ОПРЕДЕЛЕНИЕ РАСКЛАДОК (HKL) ─────────────────────────────────────
def _all_hkls() -> list[int]:
    n = user32.GetKeyboardLayoutList(0, None)
    if not n:
        return []
    arr = (ctypes.c_void_p * n)()
    user32.GetKeyboardLayoutList(n, arr)
    return [h for h in arr if h]


def _find_hkl(langid: int) -> int | None:
    for h in _all_hkls():
        if (h & 0xFFFF) == langid:
            return h
    return user32.LoadKeyboardLayoutW(f"{langid:08X}", KLF_ACTIVATE) or None


def _get_target_hwnd_and_tid() -> tuple[int | None, int]:
    """Возвращает целевое окно фокуса (или активное окно) и ID его потока."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, 0
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gi = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    target = hwnd
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gi)) and gi.hwndFocus:
        target = gi.hwndFocus
        tid = user32.GetWindowThreadProcessId(target, None) or tid
    return target, tid


def _detect_current_lang() -> str | None:
    """Определяет текущий язык ('RU' или 'EN') активного окна."""
    _, tid = _get_target_hwnd_and_tid()
    if not tid:
        return None
    hkl = user32.GetKeyboardLayout(tid)
    if not hkl:
        return None
    langid = hkl & 0xFFFF
    if langid == 0x0419:
        return "RU"
    if (langid & 0x00FF) == 0x09:  # Любой вариант английского (en-US, en-GB и т.д.)
        return "EN"
    return None


hkl_ru = _find_hkl(0x0419)  # ru-RU
hkl_en = _find_hkl(0x0409)  # en-US


# ─── СОСТОЯНИЕ ───────────────────────────────────────────────────────────────
class _State:
    enabled = True
    cur = _detect_current_lang() or "EN"
    count = 0
    lc = False        # Левый Ctrl нажат
    rc = False        # Правый Ctrl нажат
    combo = False     # Была нажата другая клавиша во время удержания Ctrl
    t_down = 0        # Время нажатия Ctrl (мс)


S = _State()
_main_tid = kernel32.GetCurrentThreadId()
_switch_queue: queue.SimpleQueue[tuple[int | None, str] | None] = queue.SimpleQueue()
_mutex_handle = None


# ─── ПЕРЕКЛЮЧЕНИЕ И СИНХРОНИЗАЦИЯ ────────────────────────────────────────────
def _do_switch(hkl: int | None, name: str):
    if not hkl or not S.enabled:
        return

    target, _ = _get_target_hwnd_and_tid()
    if target:
        # Асинхронный PostMessageW не блокирует поток, если окно зависло
        user32.PostMessageW(target, WM_INPUTLANGCHANGEREQUEST, 0, hkl)

    S.cur = name
    S.count += 1
    _tray_update()


def _worker_loop():
    """Фоновый воркер для переключения раскладки и обновления иконки."""
    while True:
        item = _switch_queue.get()
        if item is None:
            break
        hkl, name = item
        _do_switch(hkl, name)


def _sync_layout_from_system():
    """Синхронизирует иконку в трее, если раскладка изменилась извне (Alt+Shift / смена окна)."""
    if not S.enabled:
        return
    real_lang = _detect_current_lang()
    if real_lang and real_lang != S.cur:
        S.cur = real_lang
        _tray_update()


# ─── ИКОНКИ-ФЛАГИ ────────────────────────────────────────────────────────────
_SZ = 64
_R = 8  # Радиус скругления


def _rounded(img: Image.Image) -> Image.Image:
    """Обрезает картинку по скруглённому прямоугольнику и добавляет рамку."""
    sz = img.width
    mask = Image.new("L", (sz, sz), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sz - 1, sz - 1], radius=_R, fill=255)
    out = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [0, 0, sz - 1, sz - 1], radius=_R,
        outline=(90, 90, 90, 160), width=1,
    )
    return out


def _flag_ru() -> Image.Image:
    """Флаг России — три горизонтальные полосы."""
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    h = _SZ // 3
    draw.rectangle([0, 0, _SZ, h], fill=(255, 255, 255))       # Белая
    draw.rectangle([0, h, _SZ, h * 2], fill=(0, 57, 166))      # Синяя
    draw.rectangle([0, h * 2, _SZ, _SZ], fill=(213, 43, 30))   # Красная
    return _rounded(img)


def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float,
          r_out: float, r_in: float, fill):
    """Рисует 5-конечную звезду."""
    pts = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=fill)


def _flag_us() -> Image.Image:
    """Флаг США — 13 полос + звёздный кантон."""
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    RED = (178, 34, 52)
    WHITE = (255, 255, 255)
    BLUE = (60, 59, 110)

    sh = _SZ / 13
    for i in range(13):
        y0 = round(i * sh)
        y1 = round((i + 1) * sh)
        draw.rectangle([0, y0, _SZ - 1, y1], fill=RED if i % 2 == 0 else WHITE)

    cw = round(_SZ * 0.40)
    ch = round(7 * sh)
    draw.rectangle([0, 0, cw, ch], fill=BLUE)

    n_rows = 9
    r_out = max(1.8, _SZ * 0.028)
    r_in = r_out * 0.42
    row_step = ch / (n_rows + 1)

    for row in range(n_rows):
        y = (row + 1) * row_step
        n_stars = 6 if row % 2 == 0 else 5
        col_step = cw / (n_stars + 1)
        for col in range(n_stars):
            x = (col + 1) * col_step
            _star(draw, x, y, r_out, r_in, WHITE)

    return _rounded(img)


def _flag_off() -> Image.Image:
    """Серый значок — приостановлено."""
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _SZ, _SZ], fill=(160, 160, 160))
    bw = _SZ // 6
    gap = _SZ // 10
    x1 = _SZ // 2 - gap // 2 - bw
    x2 = _SZ // 2 + gap // 2
    y1, y2 = _SZ // 5, _SZ * 4 // 5
    draw.rectangle([x1, y1, x1 + bw, y2], fill=(255, 255, 255))
    draw.rectangle([x2, y1, x2 + bw, y2], fill=(255, 255, 255))
    return _rounded(img)


ICON_RU = _flag_ru()
ICON_EN = _flag_us()
ICON_OFF = _flag_off()


# ─── ТРЕЙ ────────────────────────────────────────────────────────────────────
_tray: pystray.Icon | None = None


def _tray_update():
    if not _tray:
        return
    _tray.icon = (ICON_RU if S.cur == "RU" else ICON_EN) if S.enabled else ICON_OFF
    _tray.title = (
        f"RU/EN — {S.cur} | переключений: {S.count}"
        if S.enabled else "RU/EN — приостановлен"
    )


def _menu_toggle(icon, _item):
    S.enabled = not S.enabled
    if S.enabled:
        _sync_layout_from_system()
    _tray_update()


def _menu_quit(icon, _item):
    _unhook()
    _switch_queue.put(None)
    icon.stop()
    # Отправляем WM_QUIT в главный поток, чтобы разблокировать GetMessageW
    user32.PostThreadMessageW(_main_tid, WM_QUIT, 0, 0)


_menu = pystray.Menu(
    pystray.MenuItem(
        lambda _: "⏸  Приостановить" if S.enabled else "▶  Возобновить",
        _menu_toggle,
    ),
    pystray.Menu.SEPARATOR,
    pystray.MenuItem("❌ Выйти", _menu_quit),
)


def _run_tray():
    global _tray
    initial_icon = ICON_RU if S.cur == "RU" else ICON_EN
    _tray = pystray.Icon("ru_en", initial_icon, "RU/EN Switcher", _menu)
    _tray.run_detached()
    _tray_update()


# ─── КЛАВИАТУРНЫЙ ХУК ────────────────────────────────────────────────────────
_hook_handle = None


def _kb_proc(nCode: int, wParam: int, lParam: int) -> int:
    if nCode >= 0 and S.enabled:
        kbd = KBDLLHOOKSTRUCT.from_address(lParam)
        # Игнорируем программно сгенерированные нажатия (инжекты от других программ)
        if not (kbd.flags & LLKHF_INJECTED):
            vk = kbd.vkCode
            ext = bool(kbd.flags & LLKHF_EXTENDED)
            dn = wParam in (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
            up = wParam in (0x0101, 0x0105)  # WM_KEYUP, WM_SYSKEYUP

            is_lc = (vk == 0xA2) or (vk == 0x11 and not ext)
            is_rc = (vk == 0xA3) or (vk == 0x11 and ext)

            if dn:
                if is_lc and not S.lc:
                    S.lc = True
                    S.combo = False
                    S.t_down = kbd.time
                elif is_rc and not S.rc:
                    S.rc = True
                    S.combo = False
                    S.t_down = kbd.time
                elif not is_lc and not is_rc and (S.lc or S.rc):
                    # Любая другая клавиша на фоне зажатого Ctrl отменяет одиночный тап
                    S.combo = True
            elif up:
                elapsed = (kbd.time - S.t_down) & 0xFFFFFFFF
                is_short_tap = (not S.combo) and (elapsed <= TAP_TIMEOUT_MS)

                if is_lc and S.lc:
                    S.lc = False
                    if is_short_tap:
                        _switch_queue.put((hkl_ru, "RU"))
                elif is_rc and S.rc:
                    S.rc = False
                    if is_short_tap:
                        _switch_queue.put((hkl_en, "EN"))

    return user32.CallNextHookEx(_hook_handle, nCode, wParam, lParam)


# Сохраняем глобальную ссылку на callback, чтобы GC не удалил его
_kb_ref = HOOKPROC(_kb_proc)


def _unhook():
    global _hook_handle, _mutex_handle
    if _hook_handle:
        user32.UnhookWindowsHookEx(_hook_handle)
        _hook_handle = None
    if _mutex_handle:
        kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None


atexit.register(_unhook)


# ─── ГЛАВНЫЙ ПОТОК: MESSAGE LOOP + HOOK ──────────────────────────────────────
def main():
    global _hook_handle, _mutex_handle

    # 1. Защита от запуска второй копии программы
    _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\RU_EN_Switcher_v4_Mutex")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        user32.MessageBoxW(
            None,
            "Программа RU/EN Switcher уже запущена в системном трее.",
            "RU/EN Switcher",
            0x40,  # MB_ICONINFORMATION
        )
        return

    # 2. Запускаем фоновый воркер очереди переключений и иконку в трее
    threading.Thread(target=_worker_loop, daemon=True).start()
    _run_tray()

    # 3. Устанавливаем глобальный низкоуровневый хук клавиатуры
    _hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kb_ref, None, 0)
    if not _hook_handle:
        err = kernel32.GetLastError()
        user32.MessageBoxW(
            None,
            f"Не удалось установить хук клавиатуры.\nКод ошибки: {err}",
            "RU/EN Switcher — Ошибка",
            0x10,  # MB_ICONERROR
        )
        return

    # 4. Таймер для автосинхронизации флага в трее с реальной раскладкой активного окна
    timer_id = user32.SetTimer(None, SYNC_TIMER_ID, SYNC_INTERVAL_MS, None)

    # 5. Цикл сообщений в главном потоке (обязателен для работы WH_KEYBOARD_LL)
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                _sync_layout_from_system()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if timer_id:
            user32.KillTimer(None, timer_id)
        _unhook()


if __name__ == "__main__":
    main()