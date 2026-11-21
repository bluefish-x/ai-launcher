# -*- coding: utf-8 -*-
"""
快捷启动台 —— 浅色绿色系的本地程序启动器。

把一个程序（exe / bat / 网址 / 文件夹）做成一张圆角卡片，点一下就启动。
配置存在同目录的 apps.json。

设计：低饱和绿主色 + 米白/浅灰辅助色，圆角、软阴影、充足留白、扁平化。
几何尺寸按 96dpi 设计，运行时按系统 DPI 整体缩放（字体用 pt，随 DPI 自动缩放）。

依赖：Python 3.9+（tkinter）、Pillow（圆角渲染、软阴影、exe 图标提取）
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from ctypes import wintypes, byref

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
    HAS_PIL = True
except Exception:  # pragma: no cover
    HAS_PIL = False


def _system_scale():
    """系统 DPI 缩放比例（96dpi = 1.0）。"""
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


S = _system_scale()


def px(v):
    """设计像素 -> 实际像素。"""
    return int(round(v * S))


APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
CONFIG_PATH = os.path.join(APP_DIR, "apps.json")
FONT_FAMILY = "Microsoft YaHei UI"

# ---------------------------------------------------------------- 配色
BG = "#F4F7F4"            # 页面底色：带绿意的米白
SURFACE = "#FFFFFF"       # 卡片
SURFACE_HOVER = "#E1EDE6"
FIELD = "#F3F7F3"         # 输入框填充
BORDER = "#DCE6DE"        # 描边
PRIMARY = "#6F9E82"       # 主色：低饱和鼠尾草绿
PRIMARY_DARK = "#5B8A6E"
PRIMARY_SOFT = "#E9F2EC"  # 主色浅底
PRIMARY_TINT = "#D9E9DF"
TEXT = "#2E3A32"
TEXT_MID = "#5F6C63"
MUTED = "#93A096"
SHADOW = (74, 96, 80, 26)

# ---------------------------------------------------------------- 布局（设计像素）
CARD_W, CARD_H = 356, 150
BODY_W, BODY_H = 336, 128
BODY_X, BODY_Y = 10, 8
BODY_R = 16
GAP_X, GAP_Y = 8, 8
PAGE_PAD = 28
ICON_SIZE = 54

DEFAULT_APPS = [
    {
        "name": "示例 · 程序",
        "path": r"C:\Windows\System32\notepad.exe",
        "workdir": r"C:\Windows\System32",
        "args": "",
        "type": "exe",
        "category": "示例",
        "color": "#6F9E82",
        "desc": "改成你自己的 exe / bat 路径",
    },
    {
        "name": "示例 · 网址",
        "path": "http://127.0.0.1:8188",
        "workdir": "",
        "args": "",
        "type": "web",
        "category": "示例",
        "color": "#8FA9B8",
        "desc": "用默认浏览器打开",
    },
    {
        "name": "示例 · 文件夹",
        "path": r"D:\AI",
        "workdir": "",
        "args": "",
        "type": "folder",
        "category": "示例",
        "color": "#B9A88C",
        "desc": "用资源管理器打开",
    },
]

TYPE_CHOICES = [("exe", "程序"), ("web", "网址"), ("folder", "文件夹")]
TYPE_LABEL = dict(TYPE_CHOICES)


# ---------------------------------------------------------------- 圆角 / 阴影
_IMG_CACHE = {}


def _pil_font(size, bold=False):
    for name in (("msyhbd.ttc", "msyh.ttc") if bold else ("msyh.ttc", "msyhbd.ttc")):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("segoeui.ttf", size)
    except Exception:
        return ImageFont.load_default()


def rr_image(w, h, r, fill, outline=None, outline_width=0, shadow=None,
             shadow_blur=8, shadow_dy=3, inset=0):
    """抗锯齿圆角矩形（可选柔和阴影）。inset 为四周边距，给阴影留出空间。"""
    key = ("rr", w, h, r, fill, outline, outline_width, shadow, shadow_blur, shadow_dy, inset)
    cached = _IMG_CACHE.get(key)
    if cached is not None:
        return cached

    ss = 4
    W, H = max(1, w * ss), max(1, h * ss)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    box = [inset * ss, inset * ss, W - 1 - inset * ss, H - 1 - inset * ss]
    if box[2] <= box[0] or box[3] <= box[1]:
        box = [0, 0, W - 1, H - 1]

    if shadow:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle([box[0], box[1] + shadow_dy * ss, box[2], box[3] + shadow_dy * ss],
                            radius=r * ss, fill=shadow)
        layer = layer.filter(ImageFilter.GaussianBlur(shadow_blur * ss))
        canvas.alpha_composite(layer)

    body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(body)
    d.rounded_rectangle(box, radius=r * ss, fill=fill, outline=outline,
                        width=int(outline_width * ss) if outline else 0)
    canvas.alpha_composite(body)

    out = canvas.resize((w, h), Image.LANCZOS)
    _IMG_CACHE[key] = out
    return out


def round_icon(img, size, radius_ratio=0.28):
    if img is None:
        return None
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1],
                                           radius=int(big * radius_ratio), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def ellipsize(text, font, max_width):
    if not text:
        return ""
    if font.measure(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if font.measure(text[:mid] + "…") <= max_width:
            lo = mid + 1
        else:
            hi = mid
    return text[:max(0, lo - 1)] + "…"


# ---------------------------------------------------------------- Win32：图标与进程
class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class _BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _setup_winapi():
    """显式声明参数类型，否则 64 位句柄会被截断，图标提取会随机失败。"""
    user32, gdi32, shell32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32
    user32.GetDpiForSystem.restype = wintypes.UINT
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
        wintypes.UINT, wintypes.UINT,
    ]
    user32.PrivateExtractIconsW.restype = wintypes.UINT
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetObjectW.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(_BMIH), wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    shell32.SHGetFileInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_SHFILEINFOW),
        wintypes.UINT, wintypes.UINT,
    ]
    shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4


def _hicon_to_image(hicon):
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    info = _ICONINFO()
    if not user32.GetIconInfo(hicon, byref(info)):
        return None
    try:
        bmp = _BITMAP()
        if not gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(_BITMAP), byref(bmp)):
            return None
        width, height = bmp.bmWidth, bmp.bmHeight
        if width <= 0 or height <= 0:
            return None
        hdc = user32.GetDC(None)
        memdc = gdi32.CreateCompatibleDC(hdc)
        old = gdi32.SelectObject(memdc, info.hbmColor)
        header = _BMIH()
        header.biSize = ctypes.sizeof(_BMIH)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memdc, info.hbmColor, 0, height, buf, byref(header), 0)
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, hdc)
        return Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).copy()
    except Exception:
        return None
    finally:
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)
        user32.DestroyIcon(hicon)


try:
    _setup_winapi()
except Exception:
    pass


def load_icon(path, size):
    if not HAS_PIL or not path or not os.path.exists(path):
        return None
    icon = wintypes.HICON()
    try:
        count = ctypes.windll.user32.PrivateExtractIconsW(
            ctypes.c_wchar_p(path), 0, size * 2, size * 2, byref(icon), None, 1, 0)
        if count and icon.value:
            img = _hicon_to_image(icon)
            if img:
                return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    try:
        info = _SHFILEINFOW()
        res = ctypes.windll.shell32.SHGetFileInfoW(
            ctypes.c_wchar_p(path), 0, byref(info), ctypes.sizeof(info), 0x100)
        if res and info.hIcon:
            img = _hicon_to_image(info.hIcon)
            if img:
                return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    return None


def letter_icon(text, color, size):
    if not HAS_PIL:
        return None
    img = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1], radius=int(size * 4 * 0.28),
                        fill=color)
    d.text((size * 2, size * 2 - size * 0.05), (text or "?").strip()[:1].upper(),
           font=_pil_font(int(size * 2.1), True), fill=(255, 255, 255, 245), anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def web_icon(color, size):
    if not HAS_PIL:
        return None
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.28), fill=color)
    c, r = s / 2, s * 0.28
    w = max(2, int(s * 0.034))
    white = (255, 255, 255, 235)
    d.ellipse([c - r, c - r, c + r, c + r], outline=white, width=w)
    d.ellipse([c - r * 0.42, c - r, c + r * 0.42, c + r], outline=white, width=w)
    d.line([c - r, c, c + r, c], fill=white, width=w)
    d.line([c, c - r, c, c + r], fill=white, width=w)
    return img.resize((size, size), Image.LANCZOS)


def running_images():
    names = set()
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in (0, -1):
            return names
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, byref(entry))
        k32.CloseHandle(snap)
    except Exception:
        pass
    return names


def child_processes(pid):
    out = []
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in (0, -1):
            return out
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, byref(entry))
        while ok:
            if entry.th32ParentProcessID == pid:
                out.append((entry.th32ProcessID, entry.szExeFile.lower()))
            ok = k32.Process32NextW(snap, byref(entry))
        k32.CloseHandle(snap)
    except Exception:
        pass
    return out


def process_start_time(pid):
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = k32.GetProcessTimes(handle, byref(created), byref(exited), byref(kernel), byref(user))
        k32.CloseHandle(handle)
        if not ok:
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 1e7 - 11644473600
    except Exception:
        return None


_GENERIC_IMAGES = {"python.exe", "pythonw.exe", "cmd.exe", "powershell.exe",
                   "conhost.exe", "explorer.exe", "java.exe"}


def app_image_name(app):
    path = app.get("path", "")
    if app.get("type") != "exe" or not path:
        return None
    name = os.path.basename(path).lower()
    return None if name in _GENERIC_IMAGES else name


# ---------------------------------------------------------------- 配置
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            # utf-8-sig：允许别人用带 BOM 的编辑器/脚本改过配置文件
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            apps = data.get("apps", [])
            return {"apps": apps if isinstance(apps, list) else [], "window": data.get("window", {})}
        except Exception:
            pass
    return {"apps": [dict(a) for a in DEFAULT_APPS], "window": {}}


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------- 基础控件
class PillButton(tk.Canvas):
    """圆角按钮：primary 实心绿 / soft 浅绿 / ghost 描边。"""

    def __init__(self, master, text, command=None, width=96, height=34, radius=None,
                 variant="primary", font=None, bg=None):
        w, h = px(width), px(height)
        super().__init__(master, width=w, height=h, bd=0, highlightthickness=0,
                         bg=bg or BG, cursor="hand2")
        self.var_width, self.var_height = w, h
        self.radius = px(radius) if radius is not None else h // 2
        self.command = command
        self.variant = variant
        self.label = text
        self.font = font
        self._hover = False
        self._draw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def _colors(self):
        if self.variant == "primary":
            return (PRIMARY_DARK if self._hover else PRIMARY), "#FFFFFF", None
        if self.variant == "soft":
            return (PRIMARY_TINT if self._hover else PRIMARY_SOFT), PRIMARY_DARK, None
        if self.variant == "ghost":
            return (PRIMARY_SOFT if self._hover else SURFACE), TEXT_MID, BORDER
        return SURFACE, TEXT, BORDER

    def _draw(self):
        self.delete("all")
        fill, fg, outline = self._colors()
        shadow = (*SHADOW[:3], 24) if self.variant == "primary" else None
        img = rr_image(self.var_width, self.var_height, self.radius, fill,
                       outline=outline, outline_width=1, shadow=shadow,
                       shadow_blur=px(5), shadow_dy=px(2))
        self._photo = ImageTk.PhotoImage(img)
        self.create_image(0, 0, anchor="nw", image=self._photo)
        self.create_text(self.var_width / 2, self.var_height / 2 + px(1), text=self.label,
                         fill=fg, font=self.font)

    def set_text(self, text=None, variant=None):
        if text is not None:
            self.label = text
        if variant is not None:
            self.variant = variant
        self._draw()

    def _enter(self, _e=None):
        self._hover = True
        self._draw()

    def _leave(self, _e=None):
        self._hover = False
        self._draw()

    def _click(self, _e=None):
        if self.command:
            self.command()


class RoundedEntry(tk.Canvas):
    """圆角输入框：Canvas 画底 + 内嵌无边框 Entry。"""

    def __init__(self, master, textvariable=None, width=240, height=36, radius=18,
                 font=None, bg=None, glyph=None):
        w, h = px(width), px(height)
        super().__init__(master, width=w, height=h, bd=0, highlightthickness=0,
                         bg=bg or BG)
        self.w, self.h, self.r = w, h, px(radius)
        self.var = textvariable or tk.StringVar()
        self.glyph = glyph
        self._focused = False
        self._draw()
        self.entry = tk.Entry(self, textvariable=self.var, bd=0, relief="flat",
                              bg=FIELD, fg=TEXT, insertbackground=PRIMARY_DARK,
                              font=font, highlightthickness=0)
        self._place_entry()
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def _pad_left(self):
        return px(40) if self.glyph else px(15)

    def _place_entry(self):
        y = (self.h - px(20)) // 2
        self.entry.place(x=self._pad_left(), y=y, width=max(px(10), self.w - self._pad_left() - px(14)),
                         height=px(20))

    def _draw(self):
        self.delete("all")
        fill = SURFACE if self._focused else FIELD
        outline = PRIMARY if self._focused else BORDER
        img = rr_image(self.w, self.h, self.r, fill, outline=outline,
                       outline_width=max(1, px(1.6)) if self._focused else 1)
        self._photo = ImageTk.PhotoImage(img)
        self.create_image(0, 0, anchor="nw", image=self._photo)
        if self.glyph == "search":
            cx, cy = px(20), self.h / 2 - px(1)
            r = px(5)
            lw = max(1, px(1.4))
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=MUTED, width=lw)
            self.create_line(cx + r * 0.72, cy + r * 0.72, cx + r * 1.9, cy + r * 1.9,
                             fill=MUTED, width=lw)

    def resize(self, width):
        self.w = px(width)
        self.configure(width=self.w)
        self._draw()
        self._place_entry()

    def _focus_in(self, _e=None):
        self._focused = True
        self._draw()

    def _focus_out(self, _e=None):
        self._focused = False
        self._draw()

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value or "")


class Segmented(tk.Canvas):
    """分段选择器。"""

    def __init__(self, master, choices, value=None, width=384, height=36, font=None,
                 bg=None, on_change=None):
        w, h = px(width), px(height)
        super().__init__(master, width=w, height=h, bd=0, highlightthickness=0,
                         bg=bg or BG, cursor="hand2")
        self.choices = choices
        self.w, self.h = w, h
        self.value = value or choices[0][0]
        self.font = font
        self.on_change = on_change
        self._draw()
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.delete("all")
        track = rr_image(self.w, self.h, self.h // 2, FIELD, outline=BORDER, outline_width=1)
        self._track_photo = ImageTk.PhotoImage(track)
        self.create_image(0, 0, anchor="nw", image=self._track_photo)
        pad = px(4)
        seg_w = (self.w - pad * 2) / len(self.choices)
        for i, (val, label) in enumerate(self.choices):
            x1 = pad + i * seg_w
            if val == self.value:
                active = rr_image(int(seg_w) - 2, self.h - pad * 2, (self.h - pad * 2) // 2,
                                  SURFACE, outline=BORDER, outline_width=1,
                                  shadow=(*SHADOW[:3], 20), shadow_blur=px(4), shadow_dy=px(1))
                self._active_photo = ImageTk.PhotoImage(active)
                self.create_image(int(x1) + 1, pad, anchor="nw", image=self._active_photo)
            self.create_text(x1 + seg_w / 2, self.h / 2 + px(1), text=label,
                             fill=TEXT if val == self.value else MUTED, font=self.font)

    def _click(self, event):
        pad = px(4)
        seg_w = (self.w - pad * 2) / len(self.choices)
        idx = int(max(0, min(len(self.choices) - 1, (event.x - pad) // seg_w)))
        self.value = self.choices[idx][0]
        self._draw()
        if self.on_change:
            self.on_change(self.value)

    def get(self):
        return self.value

    def set(self, value):
        if value:
            self.value = value
            self._draw()


# ---------------------------------------------------------------- 卡片
class AppCard(tk.Canvas):
    def __init__(self, master, app, launcher, fonts):
        self.cw, self.ch = px(CARD_W), px(CARD_H)
        super().__init__(master, width=self.cw, height=self.ch, bd=0, highlightthickness=0,
                         bg=BG, cursor="hand2")
        self.app = app
        self.launcher = launcher
        self.f = fonts
        self.selected = False
        self.hover = False
        self.running = False
        self._icon_photo = None
        self._ph_photo = None
        self.draw()
        for seq, cb in (("<Enter>", self._on_enter), ("<Leave>", self._on_leave),
                        ("<Button-1>", self._on_click), ("<Double-Button-1>", self._on_double),
                        ("<Button-3>", self._on_right)):
            self.bind(seq, cb)

    def _body_box(self):
        return px(BODY_X), px(BODY_Y), px(BODY_W), px(BODY_H)

    def draw(self):
        self.delete("all")
        ox, oy, bw, bh = self._body_box()
        fill = SURFACE_HOVER if self.hover and not self.selected else SURFACE
        ring_w = max(1, px(1.6))

        card = rr_image(self.cw, self.ch, px(BODY_R), fill,
                        outline=(PRIMARY if self.selected else None),
                        outline_width=ring_w,
                        shadow=(*SHADOW[:3], 34 if (self.hover or self.selected) else SHADOW[3]),
                        shadow_blur=px(7 if not (self.hover or self.selected) else 9),
                        shadow_dy=px(3))
        self._body_photo = ImageTk.PhotoImage(card)
        self.create_image(0, 0, anchor="nw", image=self._body_photo)

        # 图标
        icon = self._icon_photo if self._icon_photo is not None else self._placeholder()
        if icon is not None:
            self.create_image(ox + px(18), oy + px(18), anchor="nw", image=icon)

        text_x = ox + px(18) + px(ICON_SIZE) + px(16)
        right = ox + bw - px(18)
        avail = right - text_x

        name = ellipsize(self.app.get("name", "未命名"), self.f["card_name"], avail)
        self.create_text(text_x, oy + px(16), anchor="nw", text=name, fill=TEXT,
                         font=self.f["card_name"])

        cat = (self.app.get("category") or "未分类").strip()
        chip_w = int(self.f["chip"].measure(cat)) + px(18)
        chip_h = px(19)
        chip = rr_image(chip_w, chip_h, chip_h // 2, PRIMARY_SOFT)
        self._chip_photo = ImageTk.PhotoImage(chip)
        self.create_image(text_x, oy + px(42), anchor="nw", image=self._chip_photo)
        self.create_text(text_x + chip_w / 2, oy + px(42) + chip_h / 2 + px(1), text=cat,
                         fill=PRIMARY_DARK, font=self.f["chip"])

        # 启动按钮（先算位置，描述文字要避开它）
        bw_, bh_ = px(76), px(27)
        bx, by = right - bw_, oy + bh - px(15) - bh_

        desc = self.app.get("desc") or self.app.get("path", "")
        self.create_text(text_x, oy + px(66), anchor="nw",
                         text=ellipsize(desc, self.f["desc"], avail),
                         fill=MUTED, font=self.f["desc"])

        # 状态（与按钮同一基线）
        dot_y = by + bh_ / 2
        self.create_oval(ox + px(18), dot_y - px(3), ox + px(24), dot_y + px(3),
                         fill=("#63B183" if self.running else "#CFD9D1"), outline="")
        self.create_text(ox + px(32), dot_y + px(1), anchor="w",
                         text="运行中" if self.running else "未运行",
                         fill=("#4F9670" if self.running else MUTED), font=self.f["status"])
        btn_fill = PRIMARY if not self.running else PRIMARY_SOFT
        btn_fg = "#FFFFFF" if not self.running else PRIMARY_DARK
        btn = rr_image(bw_, bh_, bh_ // 2, btn_fill,
                       shadow=((*SHADOW[:3], 24) if not self.running else None),
                       shadow_blur=px(5), shadow_dy=px(2))
        self._btn_photo = ImageTk.PhotoImage(btn)
        self.create_image(bx, by, anchor="nw", image=self._btn_photo, tags=("btn",))
        self.create_text(bx + bw_ / 2, by + bh_ / 2 + px(1),
                         text="运行中" if self.running else "启动",
                         fill=btn_fg, font=self.f["btn"], tags=("btn",))
        self.tag_bind("btn", "<Button-1>", self._on_btn)

    def _placeholder(self):
        if self._ph_photo is None:
            color = self.app.get("color") or PRIMARY
            img = letter_icon(self.app.get("name", "?"), color, px(ICON_SIZE))
            self._ph_photo = ImageTk.PhotoImage(img) if img else None
        return self._ph_photo

    def set_icon(self, img):
        if img is None:
            return
        self._icon_photo = ImageTk.PhotoImage(round_icon(img, px(ICON_SIZE)))
        self.draw()

    def set_status(self, running):
        if running != self.running:
            self.running = running
            self.draw()

    def set_selected(self, flag):
        if flag != self.selected:
            self.selected = flag
            self.draw()

    def _on_enter(self, _e=None):
        self.hover = True
        self.draw()

    def _on_leave(self, _e=None):
        self.hover = False
        self.draw()

    def _on_click(self, _e=None):
        self.launcher.select(self)

    def _on_double(self, _e=None):
        self.launcher.launch(self.app)

    def _on_right(self, event):
        self.launcher.select(self)
        self.launcher.menu.post(event.x_root, event.y_root)
        return "break"

    def _on_btn(self, _e=None):
        self.launcher.toggle(self)
        return "break"


# ---------------------------------------------------------------- 编辑弹窗
class EditDialog(tk.Toplevel):
    def __init__(self, master, app=None, on_save=None):
        super().__init__(master)
        self.withdraw()
        self.title("编辑程序" if app else "添加程序")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)
        self.on_save = on_save
        self.app = dict(app) if app else {
            "name": "", "path": "", "args": "", "workdir": "",
            "type": "exe", "category": "", "color": PRIMARY, "desc": "",
        }
        f = master.fonts
        wrap = tk.Frame(self, bg=BG, padx=px(30), pady=px(26))
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text="编辑程序" if app else "添加程序", bg=BG, fg=TEXT,
                 font=f["dialog_title"], anchor="w").pack(fill="x")
        tk.Label(wrap, text="填写启动信息；工作目录留空时取路径所在文件夹",
                 bg=BG, fg=MUTED, font=f["hint"], anchor="w").pack(fill="x",
                                                                   pady=(px(5), px(16)))

        self.vars = {}
        self.fields = {}
        field_w = 384

        def row(label, key, browse=None):
            box = tk.Frame(wrap, bg=BG)
            box.pack(fill="x", pady=(0, px(11)))
            tk.Label(box, text=label, bg=BG, fg=TEXT_MID, font=f["label"],
                     anchor="w").pack(fill="x")
            line = tk.Frame(box, bg=BG)
            line.pack(fill="x", pady=(px(5), 0))
            var = tk.StringVar(value=str(self.app.get(key, "")))
            self.vars[key] = var
            entry = RoundedEntry(line, var, width=field_w - (74 if browse else 0),
                                 height=34, radius=11, font=f["field"], bg=BG)
            entry.pack(side="left")
            self.fields[key] = entry
            if browse:
                PillButton(line, browse[0], command=browse[1], width=64, height=36,
                           radius=12, variant="ghost", font=f["btn"],
                           bg=BG).pack(side="left", padx=(px(10), 0))

        row("名称", "name")
        row("路径 / 网址", "path", ("浏览", lambda: self._browse("path")))
        row("工作目录", "workdir", ("浏览", lambda: self._browse("workdir")))

        box = tk.Frame(wrap, bg=BG)
        box.pack(fill="x", pady=(0, px(11)))
        tk.Label(box, text="类型", bg=BG, fg=TEXT_MID, font=f["label"],
                 anchor="w").pack(fill="x")
        self.seg = Segmented(box, TYPE_CHOICES, value=self.app.get("type", "exe"),
                             width=field_w, height=36, font=f["field"], bg=BG)
        self.seg.pack(anchor="w", pady=(px(6), 0))

        row("启动参数", "args")
        row("分类", "category")
        row("说明", "desc")

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(px(10), 0))
        PillButton(btns, "取消", command=self.destroy, width=88, height=36, radius=18,
                   variant="ghost", font=f["btn"], bg=BG).pack(side="right")
        PillButton(btns, "保存", command=self.save, width=100, height=36, radius=18,
                   variant="primary", font=f["btn"], bg=BG).pack(side="right", padx=(0, px(10)))

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + max(px(24),
                                       (master.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.after(80, lambda: self.fields["name"].entry.focus_set())

    def _browse(self, key):
        if key == "path":
            p = filedialog.askopenfilename(
                title="选择程序",
                filetypes=[("程序", "*.exe *.bat *.cmd *.lnk *.vbs"), ("所有文件", "*.*")])
            if p:
                self.vars["path"].set(p)
                if not self.vars["workdir"].get():
                    self.vars["workdir"].set(os.path.dirname(p))
                if not self.vars["name"].get():
                    self.vars["name"].set(os.path.splitext(os.path.basename(p))[0])
        else:
            p = filedialog.askdirectory(title="选择工作目录")
            if p:
                self.vars[key].set(p)

    def save(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        data["type"] = self.seg.get()
        if not data["name"] or not data["path"]:
            messagebox.showwarning("缺少信息", "名称和路径都必须填写。", parent=self)
            return
        if data["type"] == "web" and not data["path"].startswith("http"):
            data["path"] = "http://" + data["path"]
        if data["type"] == "exe" and not data["workdir"]:
            data["workdir"] = os.path.dirname(data["path"])
        data["color"] = self.app.get("color") or PRIMARY
        if self.on_save:
            self.on_save(data)
        self.destroy()


# ---------------------------------------------------------------- 主窗口
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.apps = self.cfg["apps"]
        self.procs = {}
        self.watched = {}
        self.selected_card = None
        self.cards = []
        self._icon_cache = {}
        self._cols = 0
        self._scroll_frac = 0.0
        self.active_category = None

        self.title("快捷启动台")
        self.configure(bg=BG)
        self.minsize(px(680), px(520))
        geo = (self.cfg.get("window") or {}).get("geometry")
        if geo:
            self.geometry(geo)
        else:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(px(1120), sw - px(120)), min(px(760), sh - px(140))
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 2 - px(30), px(20))}")
        self.update_idletasks()
        self._apply_light_titlebar()
        self._init_fonts()
        self._build_ui()
        self._build_menu()
        self.refresh()
        self.bind("<F5>", lambda e: self.refresh())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._clamp_to_screen)
        self.after(300, self._poll_status)

    # ---------- 外观
    def _apply_light_titlebar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(0)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _init_fonts(self):
        fam = FONT_FAMILY
        self.fonts = {
            "logo": tkfont.Font(family=fam, size=10, weight="bold"),
            "title": tkfont.Font(family=fam, size=15, weight="bold"),
            "subtitle": tkfont.Font(family=fam, size=8),
            "card_name": tkfont.Font(family=fam, size=10, weight="bold"),
            "chip": tkfont.Font(family=fam, size=7),
            "desc": tkfont.Font(family=fam, size=7),
            "status": tkfont.Font(family=fam, size=7),
            "btn": tkfont.Font(family=fam, size=7, weight="bold"),
            "field": tkfont.Font(family=fam, size=8),
            "label": tkfont.Font(family=fam, size=8),
            "hint": tkfont.Font(family=fam, size=7),
            "footer": tkfont.Font(family=fam, size=7),
            "dialog_title": tkfont.Font(family=fam, size=12, weight="bold"),
        }

    # ---------- 骨架
    def _build_ui(self):
        self.header = tk.Canvas(self, bg=BG, height=px(84), bd=0, highlightthickness=0)
        self.header.pack(side="top", fill="x", padx=px(PAGE_PAD), pady=(px(8), 0))
        self.header.bind("<Configure>", lambda e: self._draw_header())

        self.catbar = tk.Canvas(self, bg=BG, height=px(46), bd=0, highlightthickness=0)
        self.catbar.pack(side="top", fill="x", padx=px(PAGE_PAD), pady=(px(2), 0))
        self.catbar.bind("<Configure>", lambda e: self._draw_catbar())

        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(px(PAGE_PAD), 0))
        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self.grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.bind_all("<MouseWheel>", self._on_wheel)

        self.sbar = tk.Canvas(body, bg=BG, width=px(8), bd=0, highlightthickness=0)
        self.sbar.pack(side="right", fill="y", padx=(0, px(20)))

        self.footer = tk.Canvas(self, bg=BG, height=px(40), bd=0, highlightthickness=0)
        self.footer.pack(side="bottom", fill="x", padx=px(PAGE_PAD), pady=(0, px(2)))
        self.footer.bind("<Configure>", lambda e: self._draw_footer())

    def _draw_header(self):
        h = self.header
        h.delete("all")
        w = max(h.winfo_width(), px(420))

        logo_s = px(38)
        logo = rr_image(logo_s, logo_s, px(12), PRIMARY, shadow=(*SHADOW[:3], 36),
                        shadow_blur=px(6), shadow_dy=px(2))
        self._logo_photo = ImageTk.PhotoImage(logo)
        h.create_image(0, px(16), anchor="nw", image=self._logo_photo)
        h.create_text(logo_s / 2, px(16) + logo_s / 2 + px(1), text="快", fill="#FFFFFF",
                      font=self.fonts["logo"])
        h.create_text(logo_s + px(14), px(18), anchor="w", text="快捷启动台", fill=TEXT,
                      font=self.fonts["title"])
        h.create_text(logo_s + px(14), px(42), anchor="w", text="本地程序 · 一键启动",
                      fill=MUTED, font=self.fonts["subtitle"])

        btn_w, search_w, gap = 104, 216, 12
        btn_x = w - px(btn_w)
        search_x = btn_x - px(gap) - px(search_w)

        if not hasattr(self, "search_box"):
            self.search_var = tk.StringVar()
            self.search_var.trace_add("write", lambda *a: self.refresh())
            self.search_box = RoundedEntry(h, self.search_var, width=search_w, height=36,
                                           radius=18, font=self.fonts["field"], bg=BG,
                                           glyph="search")
            self.btn_add = PillButton(h, "＋ 添加程序", command=self.add_app, width=btn_w,
                                      height=36, radius=18, variant="primary",
                                      font=self.fonts["btn"], bg=BG)
        else:
            self.search_box.resize(search_w)
            self.btn_add.var_width = px(btn_w)
            self.btn_add.configure(width=px(btn_w))
            self.btn_add._draw()
        h.create_window(search_x, px(17), anchor="nw", window=self.search_box,
                        width=px(search_w), height=px(36))
        h.create_window(btn_x, px(17), anchor="nw", window=self.btn_add,
                        width=px(btn_w), height=px(36))
        h.create_line(0, px(78), w, px(78), fill="#E9EFE9")

    def _categories(self):
        cats = []  # (value, label)
        seen = set()
        for a in self.apps:
            c = (a.get("category") or "").strip()
            if c not in seen:
                seen.add(c)
                cats.append((c, c or "未分类"))
        return cats

    def _draw_catbar(self):
        cb = self.catbar
        cb.delete("all")
        w = max(cb.winfo_width(), px(320))
        pills = [("全部", None)] + [(label, value) for value, label in self._categories()]
        x = px(2)
        y = px(8)
        h = px(30)
        gap = px(10)
        self._cat_hit = []
        for label, value in pills:
            active = (self.active_category == value) if value is not None else (self.active_category is None)
            tw = self.fonts["label"].measure(label)
            pw = tw + px(26)
            fill = PRIMARY if active else SURFACE
            fg = "#FFFFFF" if active else TEXT_MID
            outline = None if active else BORDER
            img = rr_image(pw, h, h // 2, fill, outline=outline, outline_width=1,
                           shadow=((*SHADOW[:3], 20) if active else None),
                           shadow_blur=px(4), shadow_dy=px(1))
            ph = ImageTk.PhotoImage(img)
            setattr(self, f"_catimg_{x}", ph)
            tag = f"cat_{label}"
            cb.create_image(x, y, anchor="nw", image=ph, tags=(tag,))
            cb.create_text(x + pw / 2, y + h / 2 + px(1), text=label, fill=fg,
                           font=self.fonts["label"], tags=(tag,))
            cb.tag_bind(tag, "<Button-1>", lambda e, v=value: self._on_cat_click(v))
            self._cat_hit.append((x, pw, value))
            x += pw + px(26) + gap
        if hasattr(self, "_cat_photos"):
            del self._cat_photos

    def _on_cat_click(self, value):
        self.active_category = value
        self.refresh()
        self._draw_catbar()

    def _draw_footer(self):
        f = self.footer
        f.delete("all")
        w = max(f.winfo_width(), px(320))
        total = len(self.apps)
        shown = len(self.cards)
        text = f"共 {total} 个程序" + (f" · 显示 {shown} 个" if shown != total else "")
        f.create_text(px(2), px(16), anchor="w", text=text, fill=MUTED,
                      font=self.fonts["footer"])
        f.create_text(w - px(2), px(16), anchor="e",
                      text="双击卡片启动 · 右键更多操作 · 滚轮滚动 · F5 刷新",
                      fill=MUTED, font=self.fonts["footer"])

    def _build_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg=SURFACE, fg=TEXT, activebackground=PRIMARY_SOFT,
                            activeforeground=PRIMARY_DARK, bd=0, relief="flat",
                            font=self.fonts["label"], activeborderwidth=0)
        self.menu.add_command(label="启动", command=lambda: self._menu_action("launch"))
        self.menu.add_command(label="停止运行", command=lambda: self._menu_action("stop"))
        self.menu.add_command(label="打开所在文件夹", command=lambda: self._menu_action("folder"))
        self.menu.add_separator()
        self.menu.add_command(label="编辑…", command=lambda: self._menu_action("edit"))
        self.menu.add_command(label="移除", command=lambda: self._menu_action("remove"))

    def _menu_action(self, action):
        if not self.selected_card:
            return
        app = self.selected_card.app
        if action == "launch":
            self.launch(app)
        elif action == "stop":
            self.stop_app(app)
        elif action == "folder":
            self.open_folder(app)
        elif action == "edit":
            self.edit_app(app)
        elif action == "remove":
            self.remove_app(app)

    # ---------- 布局
    def _clamp_to_screen(self):
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w = min(self.winfo_width(), sw - px(40))
            h = min(self.winfo_height(), sh - px(100))
            x = max(px(10), min(self.winfo_x(), sw - w - px(10)))
            y = max(px(10), min(self.winfo_y(), sh - h - px(60)))
            if (w, h) != (self.winfo_width(), self.winfo_height()) or \
                    (x, y) != (self.winfo_x(), self.winfo_y()):
                self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.grid_window, width=event.width)
        self._layout(event.width)

    def _layout(self, width=None):
        width = width or self.canvas.winfo_width()
        if width <= 1:
            return
        cell_w = px(CARD_W) + px(GAP_X)
        cols = max(1, (width - px(GAP_X)) // cell_w)
        changed = cols != self._cols
        self._cols = cols
        if not changed:
            self._update_scrollbar()
            return
        for i, card in enumerate(self.cards):
            card.grid(row=i // cols, column=i % cols,
                      padx=px(GAP_X) // 2, pady=px(GAP_Y) // 2, sticky="nw")
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar()

    def _update_scrollbar(self):
        try:
            self.grid_frame.update_idletasks()
            content = self.grid_frame.winfo_reqheight()
            view = self.canvas.winfo_height()
            self.sbar.delete("all")
            if content <= view + px(4) or content <= 0:
                return
            frac = min(1.0, view / content)
            track = view - px(10)
            thumb_h = max(px(40), int(track * frac))
            pos = px(5) + int((track - thumb_h) * self._scroll_frac)
            img = rr_image(px(6), thumb_h, px(3), "#D9E3DB")
            self._thumb_photo = ImageTk.PhotoImage(img)
            self.sbar.create_image(px(1), pos, anchor="nw", image=self._thumb_photo)
        except Exception:
            pass

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if not self._is_inside(widget, self.canvas):
            return
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._scroll_frac = self.canvas.yview()[0]
        self._update_scrollbar()

    def _is_inside(self, widget, container):
        while widget is not None:
            if widget is container:
                return True
            widget = getattr(widget, "master", None)
        return False

    # ---------- 列表
    def refresh(self):
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        visible = [a for a in self.apps
                   if (not query or query in a.get("name", "").lower()
                       or query in a.get("category", "").lower()
                       or query in a.get("path", "").lower())
                   and (self.active_category is None
                        or (a.get("category") or "").strip() == self.active_category)]

        for card in self.cards:
            card.destroy()
        self.cards = []
        self.selected_card = None
        self._cols = 0

        for app in visible:
            card = AppCard(self.grid_frame, app, self, self.fonts)
            card.grid(row=0, column=0, padx=px(GAP_X) // 2, pady=px(GAP_Y) // 2, sticky="nw")
            self.cards.append(card)

        self.after(1, self._layout)
        self._start_icon_loader()
        if hasattr(self, "footer"):
            self._draw_footer()
        if hasattr(self, "catbar"):
            self._draw_catbar()
        if visible:
            self.select(self.cards[0], force=True)

    def _start_icon_loader(self):
        pairs = [(card, dict(card.app)) for card in self.cards]

        def work():
            for card, app in pairs:
                key = (app.get("path", ""), app.get("color", PRIMARY), app.get("name", ""))
                img = self._icon_cache.get(key)
                if img is None:
                    img = self._make_icon(app)
                    self._icon_cache[key] = img
                try:
                    self.after(0, lambda c=card, i=img: c.set_icon(i))
                except Exception:
                    return

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _make_icon(app):
        kind = app.get("type", "exe")
        color = app.get("color") or PRIMARY
        size = px(ICON_SIZE) * 2
        if kind == "web":
            return web_icon(color, size)
        path = app.get("path", "")
        ext = os.path.splitext(path)[1].lower()
        if kind == "folder" or os.path.isdir(path):
            return load_icon(path, size) or letter_icon(app.get("name", "?"), color, size)
        if ext in (".bat", ".cmd", ".ps1", ".vbs"):
            return letter_icon(app.get("name", "?"), color, size)
        return load_icon(path, size) or letter_icon(app.get("name", "?"), color, size)

    def select(self, card, force=False):
        if self.selected_card is card and not force:
            return
        if self.selected_card is not None:
            try:
                self.selected_card.set_selected(False)
            except Exception:
                pass
        self.selected_card = card
        card.set_selected(True)

    # ---------- 启动 / 停止
    def _is_running(self, app):
        images = running_images()
        name = app.get("name", "")
        proc = self.procs.get(name)
        if proc is not None and proc.poll() is None:
            return True
        image = app_image_name(app)
        if image and image in images:
            return True
        return bool(self.watched.get(name, set()) & images)

    def launch(self, app):
        kind = app.get("type", "exe")
        path = app.get("path", "")
        name = app.get("name", path)
        if kind == "exe" and self._is_running(app):
            self._toast(f"{name} 已经在运行了")
            self._poll_status()
            return
        try:
            if kind == "web":
                webbrowser.open(path)
                self._toast(f"已在浏览器打开 {path}")
                return
            if kind == "folder" or os.path.isdir(path):
                os.startfile(path)
                self._toast(f"已打开 {path}")
                return
            if not os.path.exists(path):
                messagebox.showerror("找不到文件", f"路径不存在：\n{path}")
                return

            cwd = app.get("workdir") or os.path.dirname(path)
            if not os.path.isdir(cwd):
                cwd = os.path.dirname(path)
            args = app.get("args", "").strip()
            ext = os.path.splitext(path)[1].lower()
            flags = 0x00000010  # CREATE_NEW_CONSOLE
            if ext in (".bat", ".cmd"):
                cmd = ["cmd.exe", "/c", path]
            elif ext == ".ps1":
                cmd = ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", path]
            elif ext == ".lnk":
                os.startfile(path)
                self._toast(f"已启动 {name}")
                return
            else:
                cmd = [path]
            if args:
                cmd += args.split()
            proc = subprocess.Popen(cmd, cwd=cwd, creationflags=flags, close_fds=True)
            self.procs[name] = proc
            launched_at = time.time()
            self._toast(f"正在启动 {name}…")
            for delay in (1200, 2400, 3600, 5000):
                self.after(delay, self._capture_children, name, proc.pid, launched_at)
            self.after(1000, self._poll_status)
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror("启动失败", f"{name}\n\n{exc}")

    def _capture_children(self, name, pid, launched_at):
        kids = set()
        for cpid, cname in child_processes(pid):
            if cname in _GENERIC_IMAGES:
                continue
            started = process_start_time(cpid)
            if started is not None and started >= launched_at - 3:
                kids.add(cname)
        if kids:
            self.watched.setdefault(name, set()).update(kids)
            self._poll_status()

    def open_folder(self, app):
        path = app.get("path", "")
        if app.get("type") == "web" or not os.path.exists(path):
            messagebox.showinfo("无法定位", "该条目没有本地文件。")
            return
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])

    def toggle(self, card):
        if getattr(card, "running", False):
            self.stop_app(card.app)
        else:
            self.launch(card.app)

    def stop_app(self, app):
        name = app.get("name", "")
        if not messagebox.askyesno("结束进程", f"结束「{name}」？\n未保存的内容可能丢失。"):
            return
        no_window = 0x08000000
        try:
            proc = self.procs.get(name)
            if proc is not None and proc.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=no_window)
                self.procs.pop(name, None)
            else:
                image = app_image_name(app)
                if image:
                    subprocess.run(["taskkill", "/F", "/IM", image],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=no_window)
            for kid in self.watched.get(name, set()):
                subprocess.run(["taskkill", "/F", "/IM", kid],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=no_window)
            self.watched.pop(name, None)
            self._toast(f"已结束 {name}")
        except Exception as exc:
            messagebox.showerror("无法结束", str(exc))
        self.after(800, self._poll_status)

    # ---------- 增删改
    def add_app(self):
        EditDialog(self, None, on_save=self._append_app)

    def _append_app(self, data):
        self.apps.append(data)
        self._persist()
        self.refresh()

    def edit_app(self, app):
        def on_save(data):
            app.update(data)
            self._persist()
            self._icon_cache.clear()
            self.refresh()
        EditDialog(self, app, on_save=on_save)

    def remove_app(self, app):
        if messagebox.askyesno("移除", f"从列表移除「{app.get('name')}」？\n"
                                       f"（不会删除磁盘上的文件）"):
            self.apps.remove(app)
            self._persist()
            self.refresh()

    def _persist(self):
        self.cfg["apps"] = self.apps
        try:
            save_config(self.cfg)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    # ---------- 状态
    def _poll_status(self):
        images = running_images()
        for card in list(self.cards):
            name = card.app.get("name", "")
            proc = self.procs.get(name)
            tracked = proc is not None and proc.poll() is None
            if proc is not None and not tracked:
                self.procs.pop(name, None)
            image = app_image_name(card.app)
            running = tracked or (image is not None and image in images) \
                or bool(self.watched.get(name, set()) & images)
            card.set_status(running)
        self.after(2500, self._poll_status)

    def _toast(self, text):
        if not hasattr(self, "footer"):
            return
        try:
            self.footer.delete("toast")
            self.footer.create_text(px(2), px(28), anchor="w", text=text, fill=PRIMARY_DARK,
                                    font=self.fonts["footer"], tags=("toast",))
            self.after(4000, lambda: self.footer.delete("toast"))
        except Exception:
            pass

    # ---------- 收尾
    def _on_close(self):
        self.cfg["window"] = {"geometry": self.geometry()}
        self.cfg["apps"] = self.apps
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self.destroy()


# ---------------------------------------------------------------- 入口
def ensure_app_icon():
    if not HAS_PIL:
        return
    path = os.path.join(APP_DIR, "icon.ico")
    try:
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=62, fill=PRIMARY)
        d.text((size / 2, size / 2 - 4), "AI", font=_pil_font(118, True), fill="#FFFFFF",
               anchor="mm")
        img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        pass


def selftest():
    app = Launcher()
    app.withdraw()
    app.update()
    target = app.apps[0]
    print(f"[selftest] 条目: {target['name']} -> {target['path']}")
    app.launch(target)
    app.update()

    def check(step=0):
        app._poll_status()
        card = next((c for c in app.cards if c.app is target), None)
        print(f"[selftest] {step * 2}s running={getattr(card, 'running', None)}")
        if step >= 4:
            proc = app.procs.get(target["name"])
            if proc and proc.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for kid in app.watched.get(target["name"], set()):
                subprocess.run(["taskkill", "/F", "/IM", kid],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            app.destroy()
            return
        app.after(2000, check, step + 1)

    app.after(2000, check, 1)
    app.mainloop()


def main():
    if not HAS_PIL:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("缺少依赖",
                                 "本程序需要 Pillow 才能绘制圆角卡片与图标。\n\n"
                                 "请在命令行执行：pip install pillow")
            root.destroy()
        except Exception:
            print("需要 Pillow：pip install pillow")
        sys.exit(1)
    ensure_app_icon()
    if "--selftest" in sys.argv:
        selftest()
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ailab.launcher")
    except Exception:
        pass
    app = Launcher()
    app.mainloop()


if __name__ == "__main__":
    main()
