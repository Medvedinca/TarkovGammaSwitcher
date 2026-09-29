import ctypes
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import winreg

import pystray
import win32api
import win32con
import win32event
import win32gui
import winerror
from PIL import Image, ImageTk

APP_NAME    = "TarkovGammaSwitcher"
APP_VERSION = "1.1"

TARKOV_GAMMA    = 2.80
TARKOV_CONTRAST = 1.24

gdi32  = ctypes.windll.gdi32
user32 = ctypes.windll.user32

_gamma    = TARKOV_GAMMA
_contrast = TARKOV_CONTRAST
_monitor  = r"\\.\DISPLAY1"

# ── config ──────────────────────────────────────────────────────────────────────

CONFIG_DIR  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

def load_config():
    global _gamma, _contrast, _monitor
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        g = float(data.get("gamma", TARKOV_GAMMA))
        c = float(data.get("contrast", TARKOV_CONTRAST))
        _gamma    = min(5.0, max(0.1, g))
        _contrast = min(3.0, max(0.1, c))
        m = data.get("monitor")
        if m:
            _monitor = m
    except Exception:
        pass

def save_config():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {"gamma": round(_gamma, 2),
                 "contrast": round(_contrast, 2),
                 "monitor": _monitor},
                f, indent=2,
            )
    except Exception:
        pass

# ── theme ───────────────────────────────────────────────────────────────────────

BG          = "#121419"   # window
SURFACE     = "#1a1d23"   # cards
SURFACE_2   = "#23272f"   # inputs, plot area
BORDER      = "#2b3039"
GRID        = "#2a2e36"
TEXT        = "#e8eaef"
TEXT_DIM    = "#9aa1ad"
TEXT_MUTED  = "#636a78"
ACCENT      = "#d9a54a"   # tarkov amber
ACCENT_HI   = "#ebb962"
ACCENT_FILL = "#2e2718"
GREEN       = "#3fae66"
GREEN_HI    = "#4cc076"
GREEN_DIM   = "#224a33"
RED         = "#f0776c"
RED_BG      = "#3a2124"
RED_BG_HI   = "#4a2729"
KEY         = "#010203"   # transparent colour for rounded corners on Win10

F_TITLE  = ("Segoe UI Semibold", 12)
F_SUB    = ("Segoe UI", 9)
F_STATE  = ("Segoe UI Semibold", 15)
F_BODY   = ("Segoe UI", 10)
F_CAPS   = ("Segoe UI Semibold", 8)
F_NUM    = ("Bahnschrift SemiBold", 11)
F_BTN    = ("Segoe UI Semibold", 10)
F_ICON   = ("Segoe UI", 10)

IS_WIN11 = sys.getwindowsversion().build >= 22000

# ── gamma ───────────────────────────────────────────────────────────────────────

def ramp_value(x, gamma, contrast):
    if x > 0:
        x = math.pow(x, 1.0 / gamma)
    x = (x - 0.5) * contrast + 0.5
    return max(0.0, min(1.0, x))

def generate_ramp(gamma, contrast):
    ramp = (ctypes.c_ushort * 768)()
    for i in range(256):
        value = int(ramp_value(i / 255.0, gamma, contrast) * 65535)
        ramp[i] = ramp[256 + i] = ramp[512 + i] = value
    return ramp

DEFAULT_RAMP = generate_ramp(1.0, 1.0)

def get_tarkov_ramp():
    return generate_ramp(_gamma, _contrast)

def _wmi_friendly_names():
    names = {}
    try:
        import win32com.client
        wmi = win32com.client.GetObject(r"winmgmts:\\.\root\wmi")
        for r in wmi.ExecQuery("SELECT * FROM WmiMonitorID"):
            inst = r.InstanceName or ""
            parts = inst.split("\\")
            code = parts[1] if len(parts) > 1 else ""
            friendly = ""
            if r.UserFriendlyName:
                friendly = "".join(chr(c) for c in r.UserFriendlyName if c)
            if code and friendly:
                names[code.upper()] = friendly.strip()
    except Exception:
        pass
    return names

def list_monitors():
    wmi_names = _wmi_friendly_names()
    monitors = []
    i = 0
    while True:
        try:
            dev = win32api.EnumDisplayDevices(None, i)
        except Exception:
            break
        if not dev.DeviceName:
            break
        i += 1
        if not (dev.StateFlags & win32con.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
            continue
        name = dev.DeviceName
        short = name.replace("\\\\.\\", "")
        label = dev.DeviceString or short

        try:
            mon = win32api.EnumDisplayDevices(name, 0)
            dev_id = mon.DeviceID or ""
            parts = dev_id.split("\\")
            code = parts[1].upper() if len(parts) > 1 else ""
            if code in wmi_names:
                label = wmi_names[code]
            elif mon.DeviceString and mon.DeviceString != "Generic PnP Monitor":
                label = mon.DeviceString
        except Exception:
            pass
        monitors.append((name, f"{short} · {label}"))
    if not monitors:
        monitors.append((r"\\.\DISPLAY1", "DISPLAY1"))
    return monitors

def set_gamma_ramp(ramp, monitor=None):
    target = monitor or _monitor
    hdc = gdi32.CreateDCW(target, target, None, None)
    if hdc:
        gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
        gdi32.DeleteDC(hdc)

def reset_all_monitors():
    for dev, _label in list_monitors():
        set_gamma_ramp(DEFAULT_RAMP, monitor=dev)

def is_tarkov_active():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    return "EscapeFromTarkov" in win32gui.GetWindowText(hwnd)

def is_tarkov_running():
    found = []
    def cb(hwnd, _):
        if not found and win32gui.IsWindowVisible(hwnd) \
                and "EscapeFromTarkov" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return bool(found)

# ── icons ───────────────────────────────────────────────────────────────────────

def _icon_path():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "icon.png")

_icon_cache = None

def load_icon():
    global _icon_cache
    if _icon_cache is None:
        _icon_cache = Image.open(_icon_path()).convert("RGBA").resize(
            (256, 256), Image.LANCZOS)
    return _icon_cache

def tray_image(active):
    img = load_icon().resize((64, 64), Image.LANCZOS)
    if active:
        return img
    # stopped: desaturated + slightly transparent
    alpha = img.getchannel("A").point(lambda a: int(a * 0.75))
    grey = img.convert("L").convert("RGBA")
    grey.putalpha(alpha)
    return grey

# ── watcher ─────────────────────────────────────────────────────────────────────

_running    = False
_applied    = False   # tarkov ramp currently on screen (watcher's view)
_thread     = None
_stop_event = threading.Event()

def watcher_loop():
    global _applied
    _applied = False
    set_gamma_ramp(DEFAULT_RAMP)
    while not _stop_event.is_set():
        tarkov = is_tarkov_active()
        if tarkov and not _applied:
            set_gamma_ramp(get_tarkov_ramp())
            _applied = True
            update_tray()
        elif not tarkov and _applied:
            set_gamma_ramp(DEFAULT_RAMP)
            _applied = False
            update_tray()
        _stop_event.wait(0.5)
    _applied = False
    set_gamma_ramp(DEFAULT_RAMP)

def start_watcher():
    global _thread, _running
    if _running:
        return
    _stop_event.clear()
    _running = True
    _thread = threading.Thread(target=watcher_loop, daemon=True)
    _thread.start()
    update_tray()
    if _ui:
        _ui.refresh_status()

def stop_watcher():
    global _running
    if not _running:
        return
    _stop_event.set()
    _running = False
    update_tray()
    if _ui:
        _ui.refresh_status()

# ── autostart (HKCU\...\Run) ────────────────────────────────────────────────────

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def _legacy_startup_bat():
    return os.path.join(
        os.environ["APPDATA"],
        r"Microsoft\Windows\Start Menu\Programs\Startup",
        "TarkovGammaSwitcher.bat",
    )

def _startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{os.path.abspath(__file__)}" --autostart'

def is_in_startup():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return os.path.exists(_legacy_startup_bat())

def _remove_legacy_startup():
    try:
        os.remove(_legacy_startup_bat())
    except OSError:
        pass

def add_to_startup(command=None):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command or _startup_command())
    _remove_legacy_startup()

def migrate_legacy_startup():
    """Move the old Startup-folder .bat into the Run key, keeping its target."""
    path = _legacy_startup_bat()
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        # line looks like: start "" /B "C:\...\app.exe" ["C:\...\main.py"]
        tail = text.split("/B", 1)[1].splitlines()[0]
        args = [a for a in tail.split('"') if a.strip()]
        if not args:
            return
        add_to_startup(" ".join(f'"{a}"' for a in args) + " --autostart")
    except (OSError, IndexError):
        pass

def remove_from_startup():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except OSError:
        pass
    _remove_legacy_startup()

def set_startup(enabled):
    try:
        if enabled:
            add_to_startup()
        else:
            remove_from_startup()
    except OSError:
        pass

# ── widgets ─────────────────────────────────────────────────────────────────────

def round_rect(c, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, splinesteps=24, **kw)

class Card(tk.Canvas):
    """Rounded panel; put child widgets into `.inner`."""
    def __init__(self, parent, ui, fill=SURFACE, border=BORDER, pad=14, radius=10):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, height=1, width=1)
        self.fill, self.border = fill, border
        self.pad, self.radius = ui.s(pad), ui.s(radius)
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(self.pad, self.pad, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._redraw)
        self.inner.bind("<Configure>", self._fit)
        ui.cards.append(self)

    def _fit(self, e):
        self.config(height=e.height + 2 * self.pad)

    def fit(self):
        self.config(height=self.inner.winfo_reqheight() + 2 * self.pad)

    def set_border(self, color):
        self.border = color
        self._redraw()

    def _redraw(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("bg")
        round_rect(self, 0, 0, w - 1, h - 1, self.radius,
                   fill=self.fill, outline=self.border, tags="bg")
        self.tag_lower("bg")
        self.itemconfigure(self._win, width=max(1, w - 2 * self.pad))

class PillButton(tk.Canvas):
    def __init__(self, parent, ui, text, command, fill, hover, fg=TEXT,
                 height=36, width=1, radius=8, font=F_BTN, bg=SURFACE):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         height=ui.s(height), width=ui.s(width) if width > 1 else 1,
                         cursor="hand2")
        self.text, self.command = text, command
        self.fill, self.hover, self.fg = fill, hover, fg
        self.radius, self.font = ui.s(radius), font
        self._over = False
        self.bind("<Configure>", self.draw)
        self.bind("<Enter>", lambda _e: self._set_over(True))
        self.bind("<Leave>", lambda _e: self._set_over(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _set_over(self, v):
        self._over = v
        self.draw()

    def _click(self, e):
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

    def set(self, text=None, fill=None, hover=None, fg=None):
        if text is not None:  self.text = text
        if fill is not None:  self.fill = fill
        if hover is not None: self.hover = hover
        if fg is not None:    self.fg = fg
        self.draw()

    def draw(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("all")
        col = self.hover if self._over else self.fill
        round_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=col, outline=col)
        self.create_text(w / 2, h / 2, text=self.text, fill=self.fg, font=self.font)

class TextLink(tk.Label):
    def __init__(self, parent, text, command, fg=TEXT_DIM, hover=TEXT, bg=SURFACE, font=F_SUB):
        super().__init__(parent, text=text, fg=fg, bg=bg, font=font, cursor="hand2")
        self._fg, self._hover = fg, hover
        self.bind("<Enter>", lambda _e: self.config(fg=self._hover))
        self.bind("<Leave>", lambda _e: self.config(fg=self._fg))
        self.bind("<Button-1>", lambda _e: command())

class Toggle(tk.Canvas):
    def __init__(self, parent, ui, value, command, bg=SURFACE):
        self.w, self.h = ui.s(38), ui.s(22)
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         width=self.w, height=self.h, cursor="hand2")
        self.value, self.command = value, command
        self.ui = ui
        self.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _e):
        self.value = not self.value
        self.draw()
        self.command(self.value)

    def set(self, value):
        self.value = value
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.w, self.h
        track = GREEN if self.value else BORDER
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=track, outline=track)
        m = self.ui.s(3)
        d = h - 2 * m
        x = w - m - d if self.value else m
        self.create_oval(x, m, x + d, m + d, fill="#ffffff" if self.value else TEXT_DIM,
                         outline="")

class Slider(tk.Canvas):
    def __init__(self, parent, ui, lo, hi, value, marker, on_change, bg=SURFACE):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         height=ui.s(26), width=1, cursor="hand2")
        self.ui = ui
        self.lo, self.hi, self.value, self.marker = lo, hi, value, marker
        self.on_change = on_change
        self.pad, self.r, self.track = ui.s(9), ui.s(7), ui.s(4)
        self._hot = False
        self.bind("<Configure>", self.draw)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Enter>", lambda _e: self._set_hot(True))
        self.bind("<Leave>", lambda _e: self._set_hot(False))
        self.bind("<MouseWheel>", self._wheel)

    def _set_hot(self, v):
        self._hot = v
        self.draw()

    def _x(self, v):
        w = self.winfo_width()
        return self.pad + (v - self.lo) / (self.hi - self.lo) * (w - 2 * self.pad)

    def _v(self, x):
        w = self.winfo_width()
        t = (x - self.pad) / max(1, w - 2 * self.pad)
        t = max(0.0, min(1.0, t))
        return round(self.lo + t * (self.hi - self.lo), 2)

    def _drag(self, e):
        self._commit(self._v(e.x))

    def _wheel(self, e):
        step = 0.1 if e.state & 0x1 else 0.01   # Shift = coarse
        self._commit(self.value + (step if e.delta > 0 else -step))

    def _commit(self, v):
        v = round(max(self.lo, min(self.hi, v)), 2)
        if v != self.value:
            self.value = v
            self.draw()
            self.on_change(v)

    def set_value(self, v):
        self.value = v
        self.draw()

    def draw(self, _e=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        cy = h / 2
        x = self._x(self.value)
        self.create_line(self.pad, cy, w - self.pad, cy, width=self.track,
                         fill=BORDER, capstyle="round")
        self.create_line(self.pad, cy, x, cy, width=self.track,
                         fill=ACCENT, capstyle="round")
        # tick at the Tarkov default
        mx = self._x(self.marker)
        t = self.ui.s(2)
        self.create_oval(mx - t, cy + self.r + t, mx + t, cy + self.r + 3 * t,
                         fill=TEXT_MUTED, outline="")
        r = self.r + (1 if self._hot else 0)
        self.create_oval(x - r, cy - r, x + r, cy + r, fill=TEXT,
                         outline=ACCENT_HI if self._hot else ACCENT, width=self.ui.s(2))

class CurveView(tk.Canvas):
    def __init__(self, parent, ui, height=84, bg=SURFACE):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         height=ui.s(height), width=1)
        self.ui = ui
        self.gamma, self.contrast = 1.0, 1.0
        self.bind("<Configure>", self.draw)

    def set(self, gamma, contrast):
        self.gamma, self.contrast = gamma, contrast
        self.draw()

    def draw(self, _e=None):
        s = self.ui.s
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        round_rect(self, 0, 0, w - 1, h - 1, s(8), fill=SURFACE_2, outline=SURFACE_2)
        p = s(8)
        x0, y0, x1, y1 = p, p, w - p, h - p
        for i in range(1, 4):
            gx = x0 + i * (x1 - x0) / 4
            gy = y0 + i * (y1 - y0) / 4
            self.create_line(gx, y0, gx, y1, fill=GRID)
            self.create_line(x0, gy, x1, gy, fill=GRID)
        self.create_line(x0, y1, x1, y0, fill=TEXT_MUTED, dash=(2, 3))
        pts = []
        n = 64
        for i in range(n + 1):
            t = i / n
            v = ramp_value(t, self.gamma, self.contrast)
            pts += [x0 + t * (x1 - x0), y1 - v * (y1 - y0)]
        self.create_polygon(pts + [x1, y1, x0, y1], fill=ACCENT_FILL, outline="")
        self.create_line(*pts, fill=ACCENT, width=s(2), smooth=True)
        self.create_text(x1 - s(2), y1 - s(2), anchor="se", fill=TEXT_MUTED, font=F_CAPS,
                         text=f"γ {self.gamma:.2f}   k {self.contrast:.2f}")

# ── popup window ────────────────────────────────────────────────────────────────

class GammaUI:
    WIDTH  = 340
    PAD    = 16
    RADIUS = 12

    def __init__(self):
        global _monitor
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Tarkov Gamma")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.corner = BG if IS_WIN11 else KEY
        self.root.configure(bg=self.corner)
        if not IS_WIN11:
            self.root.attributes("-transparentcolor", KEY)
        self.S = self.root.winfo_fpixels("1i") / 96.0

        try:
            self._app_icon = ImageTk.PhotoImage(load_icon().resize((64, 64), Image.LANCZOS))
            self.root.iconphoto(True, self._app_icon)
        except Exception:
            pass
        self._logo = ImageTk.PhotoImage(load_icon().resize((self.s(34),) * 2, Image.LANCZOS))

        self.gamma, self.contrast = _gamma, _contrast
        self.visible = False
        self.hwnd = None
        self._size = None
        self._had_focus = False
        self._auto_hidden_at = 0.0
        self._tick_n = 0
        self.cards = []

        self.monitors = list_monitors()
        idx = next((i for i, (dev, _l) in enumerate(self.monitors) if dev == _monitor), None)
        if idx is None:
            idx = 0
            _monitor = self.monitors[0][0]
        self.mon_idx = idx

        self._build()
        self.root.bind("<Escape>", lambda _e: self.hide())

    def s(self, v):
        return int(round(v * self.S))

    # ── layout ──
    def _build(self):
        s = self.s
        self.canvas = tk.Canvas(self.root, bg=self.corner, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        P = s(self.PAD)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window(P, P, window=self.content, anchor="nw",
                                  width=s(self.WIDTH) - 2 * P)
        self.content.bind("<Configure>", lambda _e: self._fit())
        c = self.content

        # header
        hdr = tk.Frame(c, bg=BG)
        hdr.pack(fill="x")
        tk.Label(hdr, image=self._logo, bg=BG).pack(side="left")
        tb = tk.Frame(hdr, bg=BG)
        tb.pack(side="left", padx=(s(10), 0))
        tk.Label(tb, text="Tarkov Gamma", font=F_TITLE, fg=TEXT, bg=BG).pack(anchor="w")
        self.hdr_sub = tk.Label(tb, text="", font=F_SUB, fg=TEXT_DIM, bg=BG)
        self.hdr_sub.pack(anchor="w")
        PillButton(hdr, self, "✕", self.hide, fill=BG, hover=SURFACE_2, fg=TEXT_DIM,
                   width=28, height=28, radius=6, font=F_ICON, bg=BG).pack(side="right", anchor="n")

        # status hero
        self.hero = Card(c, self)
        self.hero.pack(fill="x", pady=(s(14), 0))
        h = self.hero.inner
        tk.Label(h, text="АВТОПЕРЕКЛЮЧЕНИЕ", font=F_CAPS, fg=TEXT_MUTED,
                 bg=SURFACE).pack(anchor="w")
        row = tk.Frame(h, bg=SURFACE)
        row.pack(fill="x", pady=(s(2), 0))
        self.dot = tk.Canvas(row, width=s(10), height=s(10), bg=SURFACE,
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", pady=(s(3), 0))
        self.state_lbl = tk.Label(row, text="", font=F_STATE, bg=SURFACE)
        self.state_lbl.pack(side="left", padx=(s(8), 0))
        self.detail_lbl = tk.Label(h, text="", font=F_SUB, fg=TEXT_DIM, bg=SURFACE, anchor="w")
        self.detail_lbl.pack(fill="x", pady=(0, s(12)))
        self.power_btn = PillButton(h, self, "", self.on_toggle_run,
                                    fill=GREEN, hover=GREEN_HI, height=40)
        self.power_btn.pack(fill="x")

        # image settings
        img = Card(c, self)
        img.pack(fill="x", pady=(s(10), 0))
        i = img.inner
        top = tk.Frame(i, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text="ИЗОБРАЖЕНИЕ", font=F_CAPS, fg=TEXT_MUTED, bg=SURFACE).pack(side="left")
        TextLink(top, f"↺ Tarkov {TARKOV_GAMMA:.2f} / {TARKOV_CONTRAST:.2f}", self.on_reset,
                 fg=ACCENT, hover=ACCENT_HI).pack(side="right")
        self.curve = CurveView(i, self)
        self.curve.pack(fill="x", pady=(s(8), s(8)))
        self.g_slider, self.g_text = self._slider_row(
            i, "Гамма", 0.1, 5.0, TARKOV_GAMMA, self.gamma, "gamma")
        self.c_slider, self.c_text = self._slider_row(
            i, "Контраст", 0.1, 3.0, TARKOV_CONTRAST, self.contrast, "contrast")
        tk.Label(i, text="Колесо мыши — шаг 0.01 · с Shift — 0.1",
                 font=F_SUB, fg=TEXT_MUTED, bg=SURFACE, anchor="w").pack(fill="x")

        # monitor + options
        opt = Card(c, self, pad=12)
        opt.pack(fill="x", pady=(s(10), 0))
        o = opt.inner
        mrow = tk.Frame(o, bg=SURFACE)
        mrow.pack(fill="x")
        tk.Label(mrow, text="Монитор", font=F_BODY, fg=TEXT, bg=SURFACE).pack(side="left")
        sel = tk.Frame(mrow, bg=SURFACE)
        sel.pack(side="right")
        multi = len(self.monitors) > 1
        arrow_fg = TEXT_DIM if multi else SURFACE
        TextLink(sel, "‹", lambda: self._cycle_monitor(-1), fg=arrow_fg,
                 hover=ACCENT if multi else SURFACE, font=F_TITLE).pack(side="left")
        self.mon_lbl = tk.Label(sel, text="", font=F_SUB, fg=TEXT_DIM, bg=SURFACE)
        self.mon_lbl.pack(side="left", padx=s(6))
        TextLink(sel, "›", lambda: self._cycle_monitor(1), fg=arrow_fg,
                 hover=ACCENT if multi else SURFACE, font=F_TITLE).pack(side="left")
        self._divider(o)

        srow = tk.Frame(o, bg=SURFACE)
        srow.pack(fill="x")
        tk.Label(srow, text="Запускать вместе с Windows", font=F_BODY, fg=TEXT,
                 bg=SURFACE).pack(side="left")
        self.startup_toggle = Toggle(srow, self, is_in_startup(), self.on_toggle_startup)
        self.startup_toggle.pack(side="right")
        self._divider(o)

        rrow = tk.Frame(o, bg=SURFACE)
        rrow.pack(fill="x")
        rt = tk.Frame(rrow, bg=SURFACE)
        rt.pack(side="left")
        tk.Label(rt, text="Сбросить экраны", font=F_BODY, fg=TEXT, bg=SURFACE).pack(anchor="w")
        tk.Label(rt, text="Гамма 1.00 на всех мониторах", font=F_SUB, fg=TEXT_MUTED,
                 bg=SURFACE).pack(anchor="w")
        PillButton(rrow, self, "Сброс", self.on_reset_screen, fill=RED_BG, hover=RED_BG_HI,
                   fg=RED, width=76, height=30, radius=6).pack(side="right")

        # footer
        foot = tk.Frame(c, bg=BG)
        foot.pack(fill="x", pady=(s(12), 0))
        TextLink(foot, "⏻  Выйти", self.on_quit, fg=TEXT_MUTED, hover=RED, bg=BG).pack(side="left")
        tk.Label(foot, text=f"v{APP_VERSION}", font=F_SUB, fg=TEXT_MUTED, bg=BG).pack(side="right")

        self._set_monitor_label()
        self.curve.set(self.gamma, self.contrast)
        self.refresh_status()

    def _divider(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=self.s(10))

    def _slider_row(self, parent, label, lo, hi, default, value, key):
        s = self.s
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x")
        tk.Label(row, text=label, font=F_BODY, fg=TEXT, bg=SURFACE).pack(side="left")
        text = tk.StringVar(value=f"{value:.2f}")
        entry = tk.Entry(row, textvariable=text, width=5, justify="center", font=F_NUM,
                         bg=SURFACE_2, fg=ACCENT, insertbackground=TEXT, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=SURFACE_2,
                         highlightcolor=ACCENT, selectbackground=ACCENT,
                         selectforeground=BG)
        entry.pack(side="right", ipady=s(3))
        slider = Slider(parent, self, lo, hi, value, default,
                        lambda v, k=key: self._set_value(k, v, from_slider=True))
        slider.pack(fill="x", pady=(s(2), s(6)))
        commit = lambda _e, k=key, t=text, a=lo, b=hi: self._from_entry(k, t, a, b)
        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        return slider, text

    def _fit(self):
        # canvas-embedded frames don't get <Configure> while withdrawn
        for card in self.cards:
            card.fit()
        self.content.update_idletasks()
        P = self.s(self.PAD)
        w = self.s(self.WIDTH)
        h = self.content.winfo_reqheight() + 2 * P
        if self._size == (w, h):
            return
        self._size = (w, h)
        if self.visible:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.root.geometry(f"{w}x{h}")
        self.canvas.delete("frame")
        r = self.s(8 if IS_WIN11 else self.RADIUS)
        round_rect(self.canvas, 0, 0, w - 1, h - 1, r, fill=BG, outline=BORDER, tags="frame")
        self.canvas.tag_lower("frame")

    # ── values ──
    def _set_value(self, key, v, from_slider=False):
        v = round(v, 2)
        if key == "gamma":
            self.gamma = v
            self.g_text.set(f"{v:.2f}")
            if not from_slider:
                self.g_slider.set_value(v)
        else:
            self.contrast = v
            self.c_text.set(f"{v:.2f}")
            if not from_slider:
                self.c_slider.set_value(v)
        self.curve.set(self.gamma, self.contrast)
        self._apply_live()

    def _from_entry(self, key, text, lo, hi):
        cur = self.gamma if key == "gamma" else self.contrast
        try:
            v = float(text.get().replace(",", "."))
        except ValueError:
            text.set(f"{cur:.2f}")
            return
        v = round(max(lo, min(hi, v)), 2)
        if v == cur:
            text.set(f"{v:.2f}")
            return
        self._set_value(key, v)

    def _load_values(self):
        self.gamma, self.contrast = _gamma, _contrast
        self.g_text.set(f"{_gamma:.2f}")
        self.c_text.set(f"{_contrast:.2f}")
        self.g_slider.set_value(_gamma)
        self.c_slider.set_value(_contrast)
        self.curve.set(_gamma, _contrast)

    def _apply_live(self):
        set_gamma_ramp(generate_ramp(self.gamma, self.contrast))

    # ── monitor ──
    def _set_monitor_label(self):
        label = self.monitors[self.mon_idx][1]
        if len(label) > 30:
            label = label[:29] + "…"
        self.mon_lbl.config(text=label)

    def _cycle_monitor(self, d):
        global _monitor
        if len(self.monitors) < 2:
            return
        set_gamma_ramp(DEFAULT_RAMP, monitor=_monitor)
        self.mon_idx = (self.mon_idx + d) % len(self.monitors)
        _monitor = self.monitors[self.mon_idx][0]
        save_config()
        self._set_monitor_label()
        self._apply_live()

    # ── actions ──
    def on_toggle_run(self):
        if _running:
            stop_watcher()
        else:
            start_watcher()

    def on_reset(self):
        self._set_value("gamma", TARKOV_GAMMA)
        self._set_value("contrast", TARKOV_CONTRAST)

    def on_reset_screen(self):
        global _gamma, _contrast
        stop_watcher()
        reset_all_monitors()
        self.gamma, self.contrast = 1.0, 1.0
        _gamma, _contrast = 1.0, 1.0
        save_config()
        self._load_values()
        self.refresh_status()

    def on_toggle_startup(self, enabled):
        set_startup(enabled)
        self.startup_toggle.set(is_in_startup())
        update_tray()

    def on_quit(self):
        if self.visible:
            self._commit()
        stop_watcher()
        set_gamma_ramp(DEFAULT_RAMP)
        if _tray_icon:
            _tray_icon.stop()
        self.root.quit()
        self.root.destroy()

    # ── show / hide ──
    def toggle(self, anchor=None):
        if self.visible:
            self.hide()
        elif time.monotonic() - self._auto_hidden_at > 0.4:
            # (the tray click itself steals focus and auto-hides us first)
            self.show(anchor)

    def show(self, anchor=None):
        if self.visible:
            self._focus()
            return
        self._load_values()
        self.startup_toggle.set(is_in_startup())
        self.refresh_status()
        self.root.update_idletasks()
        self._fit()
        self._place(anchor)
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.update_idletasks()
        if self.hwnd is None:
            self.hwnd = user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            self._round_corners()
        self.visible = True
        self._had_focus = False
        self._focus()
        self._fade(0)
        self._tick()

    def hide(self, auto=False):
        if not self.visible:
            return
        self.root.focus_set()          # flush pending entry edits
        self._commit()
        self.root.withdraw()
        self.visible = False
        if auto:
            self._auto_hidden_at = time.monotonic()

    def _focus(self):
        try:
            if self.hwnd:
                user32.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        self.root.focus_force()

    def _fade(self, step):
        steps = 6
        if not self.visible or step > steps:
            return
        self.root.attributes("-alpha", step / steps)
        self.root.after(14, lambda: self._fade(step + 1))

    def _round_corners(self):
        if not IS_WIN11:
            return
        try:
            pref = ctypes.c_int(2)   # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self.hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def _place(self, anchor):
        w, h = self._size
        if anchor is None:
            mon = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
        else:
            mon = win32api.MonitorFromPoint(anchor, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(mon)
        wl, wt, wr, wb = info["Work"]
        ml, mt, mr, mb = info["Monitor"]
        gap = self.s(12)
        # no anchor (launch / second instance): use the tray corner of the primary monitor
        px, py = anchor if anchor is not None else (wr, wb)
        if wb < mb:          # taskbar at the bottom
            x, y = px - w // 2, wb - h - gap
        elif wt > mt:        # top
            x, y = px - w // 2, wt + gap
        elif wl > ml:        # left
            x, y = wl + gap, py - h // 2
        elif wr < mr:        # right
            x, y = wr - w - gap, py - h // 2
        else:                # auto-hidden taskbar
            x, y = wr - w - gap, wb - h - gap
        x = max(wl + gap, min(x, wr - w - gap))
        y = max(wt + gap, min(y, wb - h - gap))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _commit(self):
        global _gamma, _contrast
        _gamma, _contrast = self.gamma, self.contrast
        save_config()
        # put the screen back to whatever the watcher would show
        if _running and is_tarkov_active():
            set_gamma_ramp(get_tarkov_ramp())
        else:
            set_gamma_ramp(DEFAULT_RAMP)

    def _tick(self):
        if not self.visible:
            return
        fg = user32.GetForegroundWindow()
        root = user32.GetAncestor(fg, 2) if fg else 0
        if root == self.hwnd:
            self._had_focus = True
        elif self._had_focus:
            self.hide(auto=True)
            return
        self._tick_n += 1
        if self._tick_n % 6 == 0:
            self.refresh_status()
        self.root.after(150, self._tick)

    # ── status ──
    def refresh_status(self):
        self.dot.delete("all")
        if _running:
            color = GREEN
            self.state_lbl.config(text="Включено", fg=GREEN)
            self.hdr_sub.config(text="Работает в фоне")
            self.hero.set_border(GREEN_DIM)
            self.power_btn.set("Выключить", fill=RED_BG, hover=RED_BG_HI, fg=RED)
            if _applied:
                detail = "Tarkov в фокусе · гамма применена"
            elif is_tarkov_running():
                detail = "Tarkov запущен · гамма включится в игре"
            else:
                detail = "Ожидание запуска Escape from Tarkov"
        else:
            color = RED
            self.state_lbl.config(text="Выключено", fg=RED)
            self.hdr_sub.config(text="Остановлено")
            self.hero.set_border(BORDER)
            self.power_btn.set("Включить", fill=GREEN, hover=GREEN_HI, fg="#ffffff")
            detail = "Гамма не меняется при входе в игру"
        self.detail_lbl.config(text=detail)
        d = self.s(10)
        self.dot.create_oval(1, 1, d - 1, d - 1, fill=color, outline="")

    # thread-safe entry points
    def call(self, fn):
        self.root.after(0, fn)

    def request_show(self):
        self.call(self.show)

# ── tray (runs in a background thread) ──────────────────────────────────────────

_ui = None
_tray_icon = None

def _tray_toggle(_icon, _item):
    pos = win32api.GetCursorPos()
    _ui.call(lambda: _ui.toggle(pos))

def _tray_reset(_icon, _item):
    _ui.call(_ui.on_reset_screen)

def _tray_startup(_icon, _item):
    _ui.call(lambda: _ui.on_toggle_startup(not is_in_startup()))

def build_menu():
    return pystray.Menu(
        pystray.MenuItem("Открыть", _tray_toggle, default=True),
        pystray.MenuItem("Автопереключение", lambda *_: _ui.call(_ui.on_toggle_run),
                         checked=lambda _i: _running),
        pystray.MenuItem("Запускать с Windows", _tray_startup,
                         checked=lambda _i: is_in_startup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Сбросить экраны", _tray_reset),
        pystray.MenuItem("Выход", lambda *_: _ui.call(_ui.on_quit)),
    )

def _tray_title():
    if not _running:
        return "Tarkov Gamma — выключено"
    if _applied:
        return "Tarkov Gamma — гамма применена"
    return "Tarkov Gamma — ожидание Tarkov"

def update_tray():
    icon = _tray_icon
    if not icon:
        return
    try:
        icon.icon = tray_image(_running)
        icon.title = _tray_title()
        icon.update_menu()
    except Exception:
        pass

def run_tray():
    global _tray_icon
    _tray_icon = pystray.Icon(APP_NAME, tray_image(_running), _tray_title(), menu=build_menu())
    _tray_icon.run()

# ── single instance ─────────────────────────────────────────────────────────────

# A named mutex marks the running instance; a second launch sets a named event
# that the first instance waits on (and raises its window), then exits.
MUTEX_NAME = r"Local\TarkovGammaSwitcher.Instance"
EVENT_NAME = r"Local\TarkovGammaSwitcher.Show"

def try_become_primary():
    """Return the show-event handle if we are the first instance, else None."""
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    try_become_primary.mutex = mutex   # keep the handle alive for the process lifetime
    return win32event.CreateEvent(None, False, False, EVENT_NAME)

def signal_existing():
    """Tell the already-running instance to show its window."""
    try:
        user32.AllowSetForegroundWindow(-1)   # ASFW_ANY: let it take focus
    except Exception:
        pass
    try:
        ev = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, EVENT_NAME)
        win32event.SetEvent(ev)
    except Exception:
        pass

def listen_for_signals(event):
    while True:
        win32event.WaitForSingleObject(event, win32event.INFINITE)
        if _ui:
            _ui.request_show()

# ── entry point ─────────────────────────────────────────────────────────────────

def _enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

def main():
    global _ui
    show_event = try_become_primary()
    if show_event is None:
        # another instance is already running – ask it to show its window
        signal_existing()
        return

    _enable_dpi_awareness()
    load_config()            # restore saved gamma/contrast/monitor
    migrate_legacy_startup()
    _ui = GammaUI()
    start_watcher()
    threading.Thread(target=run_tray, daemon=True).start()
    threading.Thread(target=listen_for_signals, args=(show_event,), daemon=True).start()
    if "--autostart" not in sys.argv:
        _ui.root.after(300, _ui.show)
    _ui.root.mainloop()

if __name__ == "__main__":
    main()
