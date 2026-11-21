# -*- coding: utf-8 -*-
"""
AI 启动台 —— 一个 Steam 风格的本地软件启动器。

只做一件事：把你常用的程序（exe / bat / 网址 / 文件夹）排成一排卡片，点一下就启动。
配置保存在同目录的 apps.json，可自由增删改。

依赖：Python 3.9+（tkinter）、Pillow（用于提取 exe 图标，缺失时自动退回字母图标）
"""

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from ctypes import wintypes, byref
from urllib.parse import urlparse

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

try:
    from PIL import Image, ImageDraw, ImageTk
    HAS_PIL = True
except Exception:  # pragma: no cover
    HAS_PIL = False

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
CONFIG_PATH = os.path.join(APP_DIR, "apps.json")

# ---------------------------------------------------------------- 配色（Steam 风）
BG = "#171a21"
HEADER = "#1b2838"
CARD = "#16202d"
CARD_HOVER = "#1f3448"
CARD_ACTIVE = "#233a52"
BORDER = "#2a475e"
ACCENT = "#66c0f4"
GREEN = "#7ad46f"
GRAY = "#5a6b7a"
TEXT = "#c7d5e0"
MUTED = "#8f98a0"
WHITE = "#ffffff"

CARD_W = 322
CARD_H = 118
GAP = 14

DEFAULT_APPS = [
    {
        "name": "示例 · 程序",
        "path": r"C:\Windows\System32\notepad.exe",
        "workdir": r"C:\Windows\System32",
        "args": "",
        "type": "exe",
        "category": "示例",
        "color": "#528a5c",
        "desc": "改成你自己的 exe / bat 路径",
    },
    {
        "name": "示例 · 网址",
        "path": "http://127.0.0.1:8188",
        "workdir": "",
        "args": "",
        "type": "web",
        "category": "示例",
        "color": "#4a7fa6",
        "desc": "用默认浏览器打开",
    },
    {
        "name": "示例 · 文件夹",
        "path": r"D:\AI",
        "workdir": "",
        "args": "",
        "type": "folder",
        "category": "示例",
        "color": "#a6714a",
        "desc": "用资源管理器打开",
    },
]

TYPE_LABEL = {"exe": "程序", "web": "网址", "folder": "文件夹"}


# ---------------------------------------------------------------- 图标提取（ctypes）
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


def running_images():
    """返回当前所有进程的映像名（小写），用于判断“某个程序是否已在运行”。"""
    names = set()
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
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
    """返回以 pid 为父进程的 [(pid, 映像名)]。有些启动器是外壳，拉起真身就自己退出。"""
    out = []
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
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
    """进程创建时间（Unix 时间戳），用来排除 PID 复用带来的无关进程。"""
    try:
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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
    """这个条目对应的进程映像名；无法唯一定位时返回 None。"""
    path = app.get("path", "")
    if app.get("type") != "exe" or not path:
        return None
    name = os.path.basename(path).lower()
    if name in _GENERIC_IMAGES:
        return None
    return name


def _setup_winapi():
    """显式声明参数类型，否则 64 位句柄会被当成 32 位 int 截断，图标提取会随机失败。"""
    user32, gdi32, shell32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32
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


def _hicon_to_image(hicon):
    """把 Windows 图标句柄转成 PIL 图像（32 位 BGRA）。"""
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
        header.biHeight = -height  # 自上而下
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
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


def load_icon(path, size=72):
    """提取文件图标并缩放到 size。返回 PIL.Image 或 None。"""
    if not HAS_PIL or not path or not os.path.exists(path):
        return None
    icon = wintypes.HICON()
    try:
        # 优先 PrivateExtractIconsW，可拿到 256px 高清图标
        count = ctypes.windll.user32.PrivateExtractIconsW(
            ctypes.c_wchar_p(path), 0, size * 2, size * 2, byref(icon), None, 1, 0
        )
        if count and icon.value:
            img = _hicon_to_image(icon)
            if img:
                return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    try:
        info = _SHFILEINFOW()
        flags = 0x000000100 | 0x000000000  # SHGFI_ICON | SHGFI_LARGEICON
        res = ctypes.windll.shell32.SHGetFileInfoW(
            ctypes.c_wchar_p(path), 0, byref(info), ctypes.sizeof(info), flags
        )
        if res and info.hIcon:
            img = _hicon_to_image(info.hIcon)
            if img:
                return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    return None


def web_icon(color, size=72):
    """网址条目用画出来的地球，避免字体里没有 emoji。"""
    if not HAS_PIL:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=color)
    c, r = size / 2, size * 0.30
    w = max(2, int(size * 0.045))
    draw.ellipse([c - r, c - r, c + r, c + r], outline=(255, 255, 255, 235), width=w)
    draw.ellipse([c - r * 0.42, c - r, c + r * 0.42, c + r], outline=(255, 255, 255, 235), width=w)
    draw.line([c - r, c, c + r, c], fill=(255, 255, 255, 235), width=w)
    draw.line([c, c - r, c, c + r], fill=(255, 255, 255, 235), width=w)
    return img


def letter_icon(text, color, size=72):
    """没有图标时用首字母方块兜底。"""
    if not HAS_PIL:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=color)
    ch = (text or "?").strip()[:1].upper()
    try:
        from PIL import ImageFont
        font = None
        for name in ("msyhbd.ttc", "msyh.ttc", "segoeuib.ttf"):
            try:
                font = ImageFont.truetype(name, int(size * 0.52))
                break
            except Exception:
                continue
        draw.text((size / 2, size / 2 - size * 0.03), ch, font=font,
                  fill=(255, 255, 255, 235), anchor="mm")
    except Exception:
        draw.text((size * 0.36, size * 0.28), ch, fill=(255, 255, 255, 235))
    return img


# ---------------------------------------------------------------- 配置读写
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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


def short_path(path, limit=44):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    txt = path.replace("/", "\\")
    if len(txt) <= limit:
        return txt
    parts = txt.split("\\")
    if len(parts) > 2:
        return f"{parts[0]}\\…\\{parts[-1]}"
    return txt[: limit - 1] + "…"


# ---------------------------------------------------------------- 卡片控件
class AppCard(tk.Frame):
    def __init__(self, master, app, launcher, index):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0, cursor="hand2")
        self.app = app
        self.launcher = launcher
        self.index = index
        self._img = None

        self.configure(width=CARD_W, height=CARD_H)
        self.pack_propagate(False)
        self.grid_propagate(False)

        inner = tk.Frame(self, bg=CARD)
        inner.place(x=12, y=11, width=CARD_W - 24, height=CARD_H - 22)
        self.inner = inner

        # 图标
        self.icon_label = tk.Label(inner, bg=CARD, bd=0)
        self.icon_label.place(x=0, y=6, width=72, height=72)

        # 名称
        self.name_label = tk.Label(inner, text=app.get("name", "未命名"), bg=CARD, fg=WHITE,
                                   font=launcher.f_name, anchor="w", justify="left")
        self.name_label.place(x=84, y=4, width=CARD_W - 24 - 84, height=22)

        # 分类
        self.cat_label = tk.Label(inner, text=app.get("category", "") or "未分类", bg=CARD,
                                  fg=ACCENT, font=launcher.f_small, anchor="w")
        self.cat_label.place(x=84, y=28, width=CARD_W - 100, height=16)

        # 描述 / 路径
        self.desc_label = tk.Label(inner, text=self._subtitle(), bg=CARD, fg=MUTED,
                                   font=launcher.f_small, anchor="w")
        self.desc_label.place(x=84, y=46, width=CARD_W - 100, height=16)

        # 状态点
        self.dot = tk.Label(inner, text="●", bg=CARD, fg=GRAY, font=launcher.f_dot)
        self.dot.place(x=84, y=68, width=14, height=16)
        self.status_label = tk.Label(inner, text="未运行", bg=CARD, fg=MUTED,
                                     font=launcher.f_small, anchor="w")
        self.status_label.place(x=100, y=68, width=90, height=16)

        # 启动按钮
        self.play_btn = tk.Label(inner, text="启 动", bg=BORDER, fg=TEXT,
                                 font=launcher.f_btn, cursor="hand2")
        self.play_btn.place(x=CARD_W - 24 - 78, y=62, width=78, height=26)

        self._bind_all(self)

    def _subtitle(self):
        desc = self.app.get("desc") or ""
        if desc:
            return desc if len(desc) <= 30 else desc[:29] + "…"
        return short_path(self.app.get("path", ""), 30)

    def _bind_all(self, widget):
        for w in [widget] + self._children(widget):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)
            w.bind("<Double-Button-1>", self._on_double)
            w.bind("<Button-3>", self._on_right)
        self.play_btn.bind("<Enter>", lambda e: self.play_btn.configure(bg=ACCENT, fg="#10202b"))
        self.play_btn.bind("<Leave>", lambda e: self.play_btn.configure(bg=BORDER, fg=TEXT))
        self.play_btn.bind("<Button-1>", lambda e: self.launcher.toggle(self))

    def _children(self, widget):
        out = []
        for c in widget.winfo_children():
            out.append(c)
            out.extend(self._children(c))
        return out

    def set_bg(self, color):
        for w in [self] + self._children(self):
            try:
                w.configure(bg=color)
            except Exception:
                pass

    def _on_enter(self, _e=None):
        if self is self.launcher.selected_card:
            return
        self.set_bg(CARD_HOVER)

    def _on_leave(self, _e=None):
        if self is self.launcher.selected_card:
            self.set_bg(CARD_ACTIVE)
        else:
            self.set_bg(CARD)

    def _on_click(self, _e=None):
        self.launcher.select(self)
        # 不要 return "break"：否则同一控件上的 <Double-Button-1> 会被吞掉

    def _on_double(self, _e=None):
        self.launcher.launch(self.app)

    def _on_right(self, event):
        self.launcher.select(self)
        self.launcher.menu.post(event.x_root, event.y_root)
        return "break"

    def set_selected(self, flag):
        self.configure(highlightbackground=ACCENT if flag else BORDER)

    def set_status(self, running):
        self.running = running
        if running:
            self.dot.configure(fg=GREEN)
            self.status_label.configure(text="运行中", fg=GREEN)
            self.play_btn.configure(text="运 行 中", bg=CARD_HOVER, fg=GREEN)
        else:
            self.dot.configure(fg=GRAY)
            self.status_label.configure(text="未运行", fg=MUTED)
            self.play_btn.configure(text="启 动", bg=BORDER, fg=TEXT)
    def apply_icon(self, img):
        if img is None:
            return
        self._img = ImageTk.PhotoImage(img)
        self.icon_label.configure(image=self._img)


# ---------------------------------------------------------------- 编辑对话框
class EditDialog(tk.Toplevel):
    def __init__(self, master, app=None, on_save=None):
        super().__init__(master)
        self.title("编辑程序" if app else "添加程序")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.on_save = on_save
        self.transient(master)
        self.app = dict(app) if app else {"name": "", "path": "", "args": "", "workdir": "",
                                          "type": "exe", "category": "", "color": "#4a7fa6",
                                          "desc": ""}
        f = master.f_label
        pad = {"padx": 14, "pady": 6}

        self.vars = {}
        rows = [("name", "名称"), ("path", "路径 / 网址"), ("workdir", "工作目录"),
                ("args", "启动参数"), ("type", "类型"), ("category", "分类"), ("desc", "说明")]
        for i, (key, label) in enumerate(rows):
            tk.Label(self, text=label, bg=BG, fg=MUTED, font=f, anchor="e", width=8).grid(
                row=i, column=0, sticky="e", padx=(16, 6), pady=7)
            var = tk.StringVar(value=str(self.app.get(key, "")))
            self.vars[key] = var
            if key == "type":
                ent = tk.OptionMenu(self, var, "exe", "web", "folder")
                ent.configure(bg=CARD, fg=TEXT, activebackground=CARD_HOVER, bd=0,
                              highlightthickness=0, width=12, font=f)
                ent.grid(row=i, column=1, sticky="w", pady=7)
            else:
                ent = tk.Entry(self, textvariable=var, bg=CARD, fg=TEXT, insertbackground=TEXT,
                               bd=0, highlightthickness=1, highlightbackground=BORDER,
                               highlightcolor=ACCENT, font=f, width=44)
                ent.grid(row=i, column=1, sticky="we", padx=(0, 6), pady=7)
                if key in ("path", "workdir"):
                    tk.Button(self, text="浏览", command=lambda k=key: self.browse(k), bg=BORDER,
                              fg=TEXT, bd=0, font=f, activebackground=CARD_HOVER,
                              activeforeground=WHITE, width=6).grid(row=i, column=2, padx=(0, 14), pady=7)

        btns = tk.Frame(self, bg=BG)
        btns.grid(row=len(rows), column=0, columnspan=3, sticky="e", padx=16, pady=(10, 16))
        tk.Button(btns, text="取消", command=self.destroy, bg=BORDER, fg=TEXT, bd=0, font=f,
                  width=8, activebackground=CARD_HOVER).pack(side="right", padx=(8, 0))
        tk.Button(btns, text="保存", command=self.save, bg=ACCENT, fg="#0e1b24", bd=0, font=f,
                  width=8, activebackground="#8fd4fb").pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self._center(master)

    def _center(self, master):
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def browse(self, key):
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
        if not data["name"] or not data["path"]:
            messagebox.showwarning("缺少信息", "名称和路径都必须填写。", parent=self)
            return
        if data["type"] == "web" and not data["path"].startswith("http"):
            data["path"] = "http://" + data["path"]
        if data["type"] == "exe" and not data["workdir"]:
            data["workdir"] = os.path.dirname(data["path"])
        if self.on_save:
            self.on_save(data)
        self.destroy()


# ---------------------------------------------------------------- 主窗口
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.apps = self.cfg["apps"]
        self.procs = {}       # name -> Popen
        self.watched = {}     # name -> {子进程映像名}（外壳型启动器用）
        self.selected_card = None
        self.cards = []
        self._icon_cache = {}
        self._cols = 0

        self.title("AI 启动台")
        self.configure(bg=BG)
        self.minsize(760, 520)
        geo = (self.cfg.get("window") or {}).get("geometry")
        if geo:
            self.geometry(geo)
        else:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(1160, sw - 120), min(780, sh - 140)
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 2 - 30, 20)}")
        self.update_idletasks()
        self._apply_dark_titlebar()
        try:
            ico = os.path.join(APP_DIR, "icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

        self._init_fonts()
        self._build_ui()
        self._build_menu()
        self.refresh()
        self.bind("<F5>", lambda e: self.refresh())
        self.bind("<Configure>", self._on_resize)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._poll_status)

    # ---------- 字体
    def _apply_dark_titlebar(self):
        """让 Windows 11 的标题栏跟着变深色。"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（新旧编号）
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _init_fonts(self):
        family = "Microsoft YaHei UI"
        self.f_title = tkfont.Font(family=family, size=15, weight="bold")
        self.f_name = tkfont.Font(family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(family=family, size=8)
        self.f_label = tkfont.Font(family=family, size=9)
        self.f_btn = tkfont.Font(family=family, size=9, weight="bold")
        self.f_dot = tkfont.Font(family="Segoe UI", size=9)
        self.f_search = tkfont.Font(family=family, size=9)

    # ---------- 界面骨架
    def _build_ui(self):
        header = tk.Frame(self, bg=HEADER, height=62)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="AI 启动台", bg=HEADER, fg=WHITE, font=self.f_title).pack(
            side="left", padx=(20, 8))
        tk.Label(header, text="点击卡片启动", bg=HEADER, fg=MUTED, font=self.f_small).pack(
            side="left", pady=(6, 0))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh())
        box = tk.Frame(header, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(side="right", padx=(0, 14), pady=16)
        tk.Label(box, text="搜索", bg=CARD, fg=MUTED, font=self.f_small).pack(
            side="left", padx=(9, 4))
        tk.Entry(box, textvariable=self.search_var, bg=CARD, fg=TEXT, insertbackground=TEXT,
                 bd=0, font=self.f_search, width=18).pack(side="left", padx=(0, 8), pady=4)

        tk.Button(header, text="＋ 添加程序", command=self.add_app, bg=ACCENT, fg="#0e1b24",
                  bd=0, font=self.f_btn, activebackground="#8fd4fb", cursor="hand2",
                  padx=12).pack(side="right", padx=(0, 10), pady=17)

        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0)
        self.vbar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.grid_frame = tk.Frame(self.canvas, bg=BG)
        self.grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        footer = tk.Frame(self, bg=HEADER, height=26)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        self.footer_label = tk.Label(footer, text="", bg=HEADER, fg=MUTED, font=self.f_small,
                                     anchor="w")
        self.footer_label.pack(side="left", padx=16)

    def _build_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                            activeforeground="#0e1b24", bd=0, font=self.f_label)
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

    # ---------- 布局与刷新
    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.grid_window, width=event.width)
        self._layout(event.width)

    def _layout(self, width=None):
        width = width or self.canvas.winfo_width()
        if width <= 1:
            return
        usable = max(width - 2 * GAP, CARD_W)
        cols = max(1, usable // (CARD_W + GAP))
        changed = cols != self._cols
        self._cols = cols
        if not changed:
            return
        for i, card in enumerate(self.cards):
            card.grid(row=i // cols, column=i % cols, padx=GAP // 2, pady=GAP // 2, sticky="nw")
        self.grid_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._toggle_scrollbar()

    def _toggle_scrollbar(self):
        """内容装得下就把滚动条收起来。"""
        try:
            need = self.grid_frame.winfo_reqheight()
            have = self.canvas.winfo_height()
            if need > have + 2:
                if not self.vbar.winfo_ismapped():
                    self.vbar.pack(side="right", fill="y")
            elif self.vbar.winfo_ismapped():
                self.vbar.pack_forget()
        except Exception:
            pass

    def _on_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.canvas:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            widget = getattr(widget, "master", None)

    def refresh(self):
        query = self.search_var.get().strip().lower()
        visible = [a for a in self.apps
                   if not query or query in a.get("name", "").lower()
                   or query in a.get("category", "").lower()
                   or query in a.get("path", "").lower()]

        for card in self.cards:
            card.destroy()
        self.cards = []
        self.selected_card = None
        self._cols = 0

        for i, app in enumerate(visible):
            card = AppCard(self.grid_frame, app, self, i)
            card.grid(row=i // 3, column=i % 3, padx=GAP // 2, pady=GAP // 2, sticky="nw")
            self.cards.append(card)

        self.after(1, self._layout)
        self._start_icon_loader()
        total = len(self.apps)
        self.footer_label.configure(
            text=f"共 {total} 个程序" + (f" · 匹配 {len(visible)} 个" if query else "")
            + "    │    双击卡片 = 启动 · 右键 = 更多操作 · F5 刷新"
        )
        if visible and not self.selected_card:
            self.select(self.cards[0], hover=False)

    def _start_icon_loader(self):
        """在单个后台线程里按顺序取图标，避免并发调用 shell API 偶发失败。"""
        pairs = [(card, dict(card.app)) for card in self.cards]

        def work():
            for card, app in pairs:
                key = (app.get("path", ""), app.get("color", "#4a7fa6"), app.get("name", ""))
                if key in self._icon_cache:
                    img = self._icon_cache[key]
                else:
                    img = self._make_icon(app)
                try:
                    self.after(0, lambda c=card, k=key, i=img: self._apply_icon(c, k, i))
                except Exception:
                    return

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _make_icon(app):
        kind = app.get("type", "exe")
        color = app.get("color") or "#4a7fa6"
        if kind == "web":
            return web_icon(color)
        path = app.get("path", "")
        ext = os.path.splitext(path)[1].lower()
        if kind == "folder" or os.path.isdir(path):
            return load_icon(path) or letter_icon(app.get("name", "?"), color)
        if ext in (".bat", ".cmd", ".ps1", ".vbs"):
            # 批处理的图标是通用的齿轮，不如字母块好看
            return letter_icon(app.get("name", "?"), color)
        return load_icon(path) or letter_icon(app.get("name", "?"), color)

    def _apply_icon(self, card, key, img):
        self._icon_cache[key] = img
        try:
            card.apply_icon(img)
        except Exception:
            pass

    def select(self, card, hover=True):
        if self.selected_card is card:
            return
        if self.selected_card is not None:
            try:
                self.selected_card.set_selected(False)
                self.selected_card.set_bg(CARD)
            except Exception:
                pass
        self.selected_card = card
        card.set_selected(True)
        if hover:
            card.set_bg(CARD_ACTIVE)

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

    # ---------- 启动
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
                self._toast(f"已启动 {app.get('name')}")
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
            messagebox.showerror("启动失败", f"{app.get('name')}\n\n{exc}")

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
        """点卡片上的按钮：没运行就启动，在运行就询问是否结束。"""
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
        if messagebox.askyesno("移除", f"从列表移除「{app.get('name')}」？\n（不会删除磁盘上的文件）"):
            self.apps.remove(app)
            self._persist()
            self.refresh()

    def _persist(self):
        self.cfg["apps"] = self.apps
        try:
            save_config(self.cfg)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    # ---------- 状态轮询
    def _capture_children(self, name, pid, launched_at):
        """记录下来外壳进程拉起的子进程名，便于以后判断“是否在运行”。"""
        kids = set()
        for cpid, cname in child_processes(pid):
            if cname in _GENERIC_IMAGES:
                continue
            started = process_start_time(cpid)
            # 只认启动之后才出现的子进程，排除 PID 复用带来的无关进程
            if started is not None and started >= launched_at - 3:
                kids.add(cname)
        if kids:
            self.watched.setdefault(name, set()).update(kids)
            self._poll_status()

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
        self.footer_label.configure(text=text)
        self.after(4000, lambda: self.footer_label.configure(
            text=f"共 {len(self.apps)} 个程序    │    "
                 "双击卡片 = 启动 · 右键 = 更多操作 · F5 刷新"))

    # ---------- 收尾
    def _on_resize(self, event):
        if event.widget is self:
            self._geometry = self.geometry()

    def _on_close(self):
        self.cfg["window"] = {"geometry": self.geometry()}
        self.cfg["apps"] = self.apps
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self.destroy()


def ensure_app_icon():
    """第一次运行时生成窗口/快捷方式图标。"""
    if not HAS_PIL:
        return
    path = os.path.join(APP_DIR, "icon.ico")
    if os.path.exists(path):
        return
    try:
        from PIL import ImageFont
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=58, fill="#1b2838")
        d.rounded_rectangle([7, 7, size - 8, size - 8], radius=52, outline=ACCENT, width=7)
        try:
            font = ImageFont.truetype("msyhbd.ttc", 118)
        except Exception:
            font = ImageFont.load_default()
        d.text((size / 2, size / 2 - 4), "AI", font=font, fill=ACCENT, anchor="mm")
        img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        pass


def selftest():
    """--selftest：不显示窗口，直接验证“启动 + 状态检测”链路。"""
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
        state = getattr(card, "running", None)
        print(f"[selftest] {step * 2}s 状态: running={state} "
              f"images={sorted(i for i in running_images() if '绘世' in i or i == app_image_name(target))}")
        if step >= 4:
            proc = app.procs.get(target["name"])
            print(f"[selftest] 已跟踪进程: {proc.pid if proc else None} "
                  f"子进程={app.watched.get(target['name'])}")
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
