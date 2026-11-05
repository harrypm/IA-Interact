#!/usr/bin/env python3
"""Capture window-ONLY screenshots of the IA-Interact GUI.

Usage:
    python tests/capture_screenshots.py

Writes assets/screenshots/gui-login.png, gui-main.png,
gui-create-repository.png.

Uses the Win32 PrintWindow API (PW_RENDERFULLCONTENT): each capture renders
the window's OWN surface into a bitmap - other apps can overlap the window,
the desktop can be visible, nothing else can ever appear in the image. (The
earlier screen-grab approach leaked desktop/other apps; do not screen-grab.)

Requires the process to be DPI-aware before tkinter starts so coordinates
match physical pixels on high-DPI displays.
"""

import ctypes
import ctypes.wintypes
import importlib.util
import os
import sys

# DPI awareness FIRST - before tkinter starts.
ctypes.windll.user32.SetProcessDPIAware()

from PIL import Image  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import ia_interact_gui as gui  # noqa: E402

OUT = os.path.join(REPO, "assets", "screenshots")

PW_RENDERFULLCONTENT = 0x00000002  # Win 8.1+: include DirectX-rendered content


def capture_window(widget):
    """Render a Tk widget's toplevel window into a PIL Image (window-only)."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # Tk's winfo_id is the inner client window; the OS window frame is its ancestor.
    hwnd = user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT
    if not hwnd:
        hwnd = widget.winfo_id()

    # Physical window size from the OS window rect.
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("window has no size")

    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    gdi32.SelectObject(hdc_mem, bitmap)

    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not ok:
        user32.ReleaseDC(hwnd, hdc_window)
        raise RuntimeError("PrintWindow failed")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = width, -height  # negative = top-down rows
    bmi.biPlanes, bmi.biBitCount = 1, 32

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    # BGRA -> RGB
    return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)


def shot(widget, name):
    image = capture_window(widget)
    path = os.path.join(OUT, name)
    image.save(path)
    print(f"saved {name} {image.size} (window-only via PrintWindow)")


def main():
    os.makedirs(OUT, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "ia_gui", os.path.join(REPO, "ia_interact_gui.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    root = m.IAInteractGUI()
    state = {"dlg": None}

    root.after(1500, lambda: shot(root, "gui-login.png"))

    def login():
        root.login_frame.destroy()
        root.access_key = os.environ.get("S3_ACCESS_KEY", "TESTKEY")
        root.secret_key = os.environ.get("S3_SECRET_KEY", "TESTSECRET")
        root._build_main_screen()
        root.after(1500, lambda: shot(root, "gui-main.png"))

    def open_dialog():
        dlg = m.CreateRepositoryDialog(root)
        state["dlg"] = dlg
        dlg.title_var.set("My Cool Video (1972)")
        dlg.description_edit.insert("1.0", "A home movie digitised from VHS.")
        dlg.subjects_var.set("home movie, vhs")
        dlg.collection_var.set("Community video")
        dlg.update_idletasks()
        root.after(1200, finish)

    def finish():
        if state["dlg"] is not None:
            shot(state["dlg"], "gui-create-repository.png")
            state["dlg"].destroy()
            state["dlg"] = None
        root.destroy()
        root.quit()

    root.after(2600, login)
    root.after(4800, open_dialog)
    root.after(12000, finish)  # safety net
    root.mainloop()


if __name__ == "__main__":
    main()
