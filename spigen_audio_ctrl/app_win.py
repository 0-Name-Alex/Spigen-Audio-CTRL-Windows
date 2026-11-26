"""customtkinter desktop interface for Spigen Audio CTRL — Windows port.

Refined "Task Hub" Edition:
  • Command Center UI with live telemetry indicators
  • Direct hardware profile switching: 6 ANC profiles + Master Quick-Bar
  • Ultra-responsive 10-band DSP Equalizer with Harman Audiophile Target overlay
  • Custom preset storage and 1-click quick-chips
  • Physical button & gesture remapping engine
  • Thread-safe BLE queue and live battery tracking
  • Windows System Tray with instant ANC radio toggles
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import tkinter as tk
import tkinter.simpledialog as simpledialog
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

try:
    import pystray
    from PIL import Image as PILImage
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False

from .bluetooth_win import BluetoothService, ScannedDevice
from .protocol import EQ_FREQUENCIES, HardwareInfo


def _resource_path(relative: str) -> Path:
    """Locate a bundled asset (handles PyInstaller sys._MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
    return base / relative


# ---------------------------------------------------------------------------
# Task Hub Dark Palette
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_MAIN = "#0b0d14"             # Deep slate canvas
BG_SIDEBAR = "#111420"          # Command dock background
BG_CARD = "#171b2a"             # Elevated panel
BG_CARD_HOVER = "#202538"       # Hovered panel
BG_CARD_ACTIVE = "#281e42"      # Active selected profile
BG_DEVICE_CARD = "#1a192e"      # Device hub header

FG_TEXT = "#f9fafb"             # Crisp pure text
FG_SUBTLE = "#9ca3af"           # Secondary silver
FG_TINY = "#64748b"             # Muted metadata
FG_ACCENT = "#a855f7"           # Neon Violet
FG_ACCENT_HOVER = "#9333ea"     # Darker violet
FG_CYAN = "#06b6d4"             # Cyan indicator
FG_BATTERY = "#10b981"          # Emerald green
FG_BATTERY_LOW = "#f43f5e"      # Rose red
FG_CONNECTED = "#10b981"        # Emerald
FG_DISCONNECTED = "#f43f5e"     # Rose
FG_WARNING = "#f59e0b"          # Amber (Harman curve)
FG_GAMING = "#ec4899"           # Hot Pink

BORDER_CARD = "#23293d"         # Sleek panel border
BORDER_ACTIVE = "#a855f7"       # Active glowing violet
BORDER_DEVICE = "#4c2889"       # Device card purple outline

FONT_MAIN = ("Segoe UI", 12)
FONT_BOLD = ("Segoe UI", 12, "bold")
FONT_SMALL = ("Segoe UI", 11)
FONT_TINY = ("Segoe UI", 9)
FONT_HEADING = ("Segoe UI", 20, "bold")
FONT_SUBHEADING = ("Segoe UI", 14, "bold")
FONT_EYEBROW = ("Segoe UI", 9, "bold")
FONT_BADGE = ("Segoe UI", 10, "bold")
FONT_ICON = ("Segoe UI", 16)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
PRESETS: dict[str, list[float]] = {
    "Harman Audiophile Target": [4, 2, 0, 0, 1, 3, 4, 2, 1, 0],
    "Default Flat": [0] * 10,
    "Bass Boost": [6, 4, 2, 0, -1, -2, -1, 0, 2, 3],
    "Pop": [-1, 1, 2, 3, 2, 0, 1, 2, 3, 2],
    "Rock": [4, 2, -1, -2, 0, 1, 2, 3, 4, 4],
    "Classical": [3, 2, 1, 0, 0, 1, 2, 3, 2, 1],
    "Vocal Enhance": [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
    "Gaming Footstep & Spatial": [2, 0, -2, 1, 3, 4, 4, 3, 2, 3],
}
HARMAN_GAINS = PRESETS["Harman Audiophile Target"]

ANC_MODES = (
    ("Deep ANC", "-43 dB", "Maximum acoustic isolation for flights, transit, and loud environments.", "◉", 65794),
    ("Adaptive ANC", "Dynamic", "Continuously adapts filter coefficients to ambient noise and ear seal.", "✦", 66816),
    ("Commuting", "Transit", "Suppresses repetitive low-frequency vehicle and engine rumble.", "≋", 66048),
    ("Anti-Wind", "Outdoor", "Cancels turbulence across external forward-facing feedforward mics.", "〰", 66560),
    ("Transparency", "Ambient", "Allows voices and natural spatial awareness through external mics.", "◎", 196868),
    ("Passive Off", "Off", "Disables active processing for pure passive isolation and max battery.", "○", 131072),
)

FUNCTIONS = (
    (1, "Play / Pause"), (3, "Next Track"), (2, "Previous Track"),
    (5, "Volume Up"), (6, "Volume Down"), (4, "Voice Assistant"),
    (7, "Gaming Mode Toggle"), (8, "ANC Mode Switch"), (0, "Disabled"),
)
FUNCTION_NAMES = {fid: fname for fid, fname in FUNCTIONS}


@dataclass(frozen=True)
class RemapRow:
    badge: str
    key_name: str
    gesture: str
    key_number: int
    action: int
    default_function: int


REMAP_ROWS = (
    RemapRow("MFB", "Multi-Function Button", "Single Click", 7, 1, 1),
    RemapRow("MFB", "Multi-Function Button", "Double Click", 7, 2, 3),
    RemapRow("MFB", "Multi-Function Button", "Triple Click", 7, 3, 2),
    RemapRow("+", "Volume Up Button", "Single Click", 5, 1, 5),
    RemapRow("+", "Volume Up Button", "Long Press", 5, 5, 3),
    RemapRow("−", "Volume Down Button", "Single Click", 3, 1, 6),
    RemapRow("−", "Volume Down Button", "Long Press", 3, 5, 2),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sep(parent: Any, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER_CARD, **kw)


def _label(parent: Any, text: str, font=FONT_MAIN, fg: str = FG_TEXT,
           anchor: str = "w", **kw) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=font, text_color=fg, anchor=anchor, **kw)


# ---------------------------------------------------------------------------
# Studio EQ Canvas
# ---------------------------------------------------------------------------
class EQGraph(tk.Canvas):
    PAD_X, PAD_Y = 38, 24

    def __init__(self, parent: Any, gains: list[float], **kw) -> None:
        super().__init__(
            parent,
            bg=BG_CARD,
            bd=0, highlightthickness=0,
            height=170,
            **kw,
        )
        self.gains = gains
        self.show_harman = True
        self._drag_band = -1
        self._drag_start_y = 0.0
        self._drag_start_gain = 0.0
        self.on_gain_change: Any = None

        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)

    def _node(self, index: int, gain: float) -> tuple[float, float]:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            w, h = 600, 170
        x = self.PAD_X + index / 9 * max(1, w - self.PAD_X * 2)
        y = self.PAD_Y + (8 - gain) / 16 * max(1, h - self.PAD_Y * 2)
        return x, y

    def _curve_points(self, gains: list[float]) -> list[tuple[float, float]]:
        return [self._node(i, g) for i, g in enumerate(gains)]

    def _bezier_path(self, pts: list[tuple[float, float]]) -> list[float]:
        """Convert control points to smooth polyline via Catmull-Rom spline."""
        result: list[float] = []
        for i in range(len(pts) - 1):
            p0 = pts[max(0, i - 1)]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[min(len(pts) - 1, i + 2)]
            for t_step in range(20):
                t = t_step / 20
                x = 0.5 * (
                    2 * p1[0]
                    + (-p0[0] + p2[0]) * t
                    + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t * t
                    + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t * t * t
                )
                y = 0.5 * (
                    2 * p1[1]
                    + (-p0[1] + p2[1]) * t
                    + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t * t
                    + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t * t * t
                )
                result += [x, y]
        result += [pts[-1][0], pts[-1][1]]
        return result

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return

        # dB horizontal grid lines
        for db in (-8, -4, 0, 4, 8):
            _, y = self._node(0, db)
            is_zero = (db == 0)
            colour = "#2e344e" if is_zero else "#1c2032"
            dash = () if is_zero else (3, 5)
            self.create_line(self.PAD_X - 10, y, w - self.PAD_X + 10, y, fill=colour, width=1, dash=dash)
            db_label = f"{'+' if db > 0 else ''}{db}dB"
            self.create_text(self.PAD_X - 18, y, text=db_label, fill="#475569", font=("Segoe UI", 8), anchor="e")

        # Vertical band markers
        for i in range(10):
            x, _ = self._node(i, 0)
            self.create_line(x, self.PAD_Y - 8, x, h - self.PAD_Y + 8, fill="#181c2c", width=1)

        # Harman target reference curve (amber dashed line)
        if self.show_harman:
            pts = self._curve_points(HARMAN_GAINS)
            flat = self._bezier_path(pts)
            if len(flat) >= 4:
                self.create_line(*flat, fill=FG_WARNING, width=2, dash=(5, 4))

        # User EQ curve — gradient fill under curve
        pts = self._curve_points(self.gains)
        flat = self._bezier_path(pts)
        if len(flat) >= 4:
            x_last, y_last = self._node(9, -8)
            x_first, _ = self._node(0, -8)
            fill_pts = flat + [x_last, y_last, x_first, y_last]
            self.create_polygon(*fill_pts, fill="#241b3d", outline="")
            self.create_line(*flat, fill="#c084fc", width=2.5)

        # Draggable band handles
        for i, gain in enumerate(self.gains):
            x, y = self._node(i, gain)
            # Outer ring
            self.create_oval(x - 6, y - 6, x + 6, y + 6, fill="#a855f7", outline="#ffffff", width=1.5)
            # Center core
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#0b0d14", outline="")

    def _band_at(self, x: float) -> int:
        w = self.winfo_width() or 600
        return max(0, min(9, round((x - self.PAD_X) / max(1, w - self.PAD_X * 2) * 9)))

    def _gain_at(self, y: float) -> float:
        h = self.winfo_height() or 170
        value = 8 - (y - self.PAD_Y) / max(1, h - self.PAD_Y * 2) * 16
        rounded = round(value * 2) / 2
        return 0.0 if abs(rounded) < 1e-6 else max(-8.0, min(8.0, rounded))

    def _on_press(self, event: tk.Event) -> None:  # type: ignore[override]
        self._drag_band = self._band_at(event.x)
        self._drag_start_y = event.y
        self._drag_start_gain = self.gains[self._drag_band]
        gain = self._gain_at(event.y)
        if self.on_gain_change:
            self.on_gain_change(self._drag_band, gain)

    def _on_drag(self, event: tk.Event) -> None:  # type: ignore[override]
        if self._drag_band < 0:
            return
        h = self.winfo_height() or 170
        delta = -(event.y - self._drag_start_y) / max(1, h - self.PAD_Y * 2) * 16
        new_gain = round((self._drag_start_gain + delta) * 2) / 2
        if abs(new_gain) < 1e-6:
            new_gain = 0.0
        new_gain = max(-8.0, min(8.0, new_gain))
        if self.on_gain_change:
            self.on_gain_change(self._drag_band, new_gain)


# ---------------------------------------------------------------------------
# Device Picker Dialog
# ---------------------------------------------------------------------------
class DevicePickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, devices: list[ScannedDevice],
                 on_picked: Callable[[ScannedDevice], None]) -> None:
        super().__init__(parent)
        self.title("Pair Spigen Audio Device")
        self.geometry("500x460")
        self.resizable(False, False)
        self.configure(fg_color=BG_MAIN)
        self.grab_set()
        self.lift()
        self.focus_force()

        self._on_picked = on_picked

        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 10))
        _label(hdr, "DEVICE DISCOVERY", font=FONT_EYEBROW, fg=FG_ACCENT).pack(anchor="w")
        _label(hdr, "Select Your Headphones", font=FONT_SUBHEADING).pack(anchor="w")
        _label(hdr, f"{len(devices)} Bluetooth LE device(s) found near you", font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_CARD, corner_radius=12,
            border_width=1, border_color=BORDER_CARD,
        )
        scroll.pack(fill="both", expand=True, padx=24, pady=(6, 12))

        for dev in devices:
            self._make_row(scroll, dev)

        ctk.CTkButton(
            self, text="Close", font=FONT_SMALL,
            fg_color="transparent", text_color=FG_SUBTLE,
            hover_color=BG_CARD, corner_radius=8, height=32,
            command=self.destroy,
        ).pack(pady=(0, 18))

    @staticmethod
    def _rssi_bar(rssi: int) -> str:
        if rssi >= -60:  return "▰▰▰▰▰"
        if rssi >= -70:  return "▰▰▰▰▱"
        if rssi >= -80:  return "▰▰▰▱▱"
        if rssi >= -90:  return "▰▰▱▱▱"
        return "▰▱▱▱▱"

    def _make_row(self, parent, dev: ScannedDevice) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent", cursor="hand2", height=52)
        row.pack(fill="x", padx=4, pady=2)
        row.pack_propagate(False)

        icon = ctk.CTkLabel(row, text="🎧", font=("Segoe UI Emoji", 18), width=34)
        icon.pack(side="left", padx=(8, 0))

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(8, 0))
        name_lbl = _label(info, dev.name, font=FONT_BOLD, fg=FG_TEXT)
        name_lbl.pack(anchor="w")
        addr_lbl = _label(info, dev.address, font=FONT_TINY, fg=FG_TINY)
        addr_lbl.pack(anchor="w")

        bar_color = FG_CONNECTED if dev.rssi >= -65 else (FG_WARNING if dev.rssi >= -80 else FG_DISCONNECTED)
        bar_lbl = ctk.CTkLabel(row, text=self._rssi_bar(dev.rssi), font=("Segoe UI", 10),
                               text_color=bar_color, width=54)
        bar_lbl.pack(side="right", padx=(0, 10))

        rssi_lbl = _label(row, f"{dev.rssi} dBm", font=FONT_TINY, fg=FG_TINY, anchor="e")
        rssi_lbl.pack(side="right", padx=(0, 4))

        def _enter(_e, _r=row): _r.configure(fg_color="#241e3a")
        def _leave(_e, _r=row): _r.configure(fg_color="transparent")
        def _click(_e=None, _d=dev):
            self.destroy()
            self._on_picked(_d)

        for w in (row, icon, name_lbl, addr_lbl, bar_lbl, rssi_lbl, info):
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)
            w.bind("<Button-1>", _click)

        _sep(parent).pack(fill="x", padx=4)


# ---------------------------------------------------------------------------
# Main Task Hub Window
# ---------------------------------------------------------------------------
class MainWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spigen Audio CTRL — Task Hub")
        self.geometry("1080x720")
        self.minsize(920, 600)
        self.configure(fg_color=BG_MAIN)

        _ico = _resource_path("assets/icon.ico")
        if _ico.exists():
            try:
                self.iconbitmap(str(_ico))
            except Exception:
                pass

        # BLE service
        self.service = BluetoothService(schedule_callback=lambda fn: self.after(0, fn))
        self.service.on_connection = self._on_connection
        self.service.on_battery = self._on_battery
        self.service.on_hardware_info = self._on_hardware_info

        # State
        self.gains: list[float] = [0.0] * 10
        self._eq_after_id: str | None = None
        self._updating_hardware = False
        self.custom_presets: dict[str, list[float]] = {}
        self.presets: dict[str, list[float]] = {n: g.copy() for n, g in PRESETS.items()}
        self._presets_path = (
            Path(os.getenv("APPDATA") or os.path.expanduser("~"))
            / "SpigenAudioCTRL"
            / "presets.json"
        )
        self._load_custom_presets()

        self._slider_vars: list[tk.DoubleVar] = [tk.DoubleVar(value=0.0) for _ in range(10)]
        self._remap_vars: dict[str, tk.StringVar] = {}
        self._gaming_var = tk.BooleanVar(value=False)
        self._anc_var = tk.IntVar(value=66816)  # Adaptive ANC default
        self._show_harman_var = tk.BooleanVar(value=True)
        self._current_page = "anc"

        # Tray state
        self._tray: pystray.Icon | None = None
        self._tray_thread: threading.Thread | None = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self.service.connect)

    # ------------------------------------------------------------------
    # UI Layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()

        # Main Hub Container (Top Status Bar + Content Pages)
        main_container = ctk.CTkFrame(self, fg_color=BG_MAIN, corner_radius=0)
        main_container.grid(row=0, column=1, sticky="nsew")
        main_container.grid_rowconfigure(1, weight=1)
        main_container.grid_columnconfigure(0, weight=1)

        self._build_top_header(main_container)

        self._content_frame = ctk.CTkFrame(main_container, fg_color="transparent", corner_radius=0)
        self._content_frame.grid(row=1, column=0, sticky="nsew")
        self._content_frame.grid_rowconfigure(0, weight=1)
        self._content_frame.grid_columnconfigure(0, weight=1)

        self._pages: dict[str, ctk.CTkFrame] = {}
        self._pages["anc"] = self._build_anc_page()
        self._pages["eq"] = self._build_eq_page()
        self._pages["buttons"] = self._build_buttons_page()

        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self._show_page("anc")

    # --- Top Header Status Deck ---------------------------------------
    def _build_top_header(self, parent: ctk.CTkFrame) -> None:
        top_bar = ctk.CTkFrame(parent, fg_color=BG_SIDEBAR, height=54, corner_radius=0)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_propagate(False)

        # Brand / Title
        brand_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        brand_frame.pack(side="left", padx=24, pady=12)
        _label(brand_frame, "TASK HUB", font=FONT_EYEBROW, fg=FG_ACCENT).pack(side="left")
        _label(brand_frame, "  •  AUDIO CONTROL DECK", font=FONT_TINY, fg=FG_TINY).pack(side="left")

        # Telemetry pills on right
        telemetry = ctk.CTkFrame(top_bar, fg_color="transparent")
        telemetry.pack(side="right", padx=24, pady=10)

        # Live Profile chip
        self._chip_profile = ctk.CTkLabel(
            telemetry, text="Adaptive ANC", font=FONT_TINY,
            text_color="#c084fc", fg_color="#241b3d", corner_radius=6, padx=10, pady=4
        )
        self._chip_profile.pack(side="right", padx=(6, 0))

        # Gaming Latency chip
        self._chip_gaming = ctk.CTkLabel(
            telemetry, text="Music Mode (Standard)", font=FONT_TINY,
            text_color=FG_SUBTLE, fg_color=BG_CARD, corner_radius=6, padx=10, pady=4
        )
        self._chip_gaming.pack(side="right", padx=(6, 0))

        # Battery pill
        self._chip_battery = ctk.CTkLabel(
            telemetry, text="— %", font=FONT_TINY,
            text_color=FG_BATTERY, fg_color=BG_CARD, corner_radius=6, padx=10, pady=4
        )
        self._chip_battery.pack(side="right", padx=(6, 0))

        # Online dot
        self._chip_status = ctk.CTkLabel(
            telemetry, text="● Offline", font=FONT_TINY,
            text_color=FG_DISCONNECTED, fg_color=BG_CARD, corner_radius=6, padx=10, pady=4
        )
        self._chip_status.pack(side="right")

    # --- Sidebar Command Dock -----------------------------------------
    def _build_sidebar(self) -> None:
        sb = ctk.CTkFrame(self, fg_color=BG_SIDEBAR, corner_radius=0, width=265)
        sb.grid(row=0, column=0, sticky="ns")
        sb.grid_propagate(False)

        # ── Device Card ──────────────────────────────────────────────
        dev_card = ctk.CTkFrame(sb, fg_color=BG_DEVICE_CARD, corner_radius=14,
                                border_width=1, border_color=BORDER_DEVICE)
        dev_card.pack(fill="x", padx=14, pady=(18, 0))

        top_row = ctk.CTkFrame(dev_card, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(12, 4))

        orb = ctk.CTkFrame(top_row, fg_color="#2b1a3d", corner_radius=999, width=40, height=40)
        orb.pack(side="left")
        orb.pack_propagate(False)
        ctk.CTkLabel(orb, text="🎧", font=("Segoe UI Emoji", 16)).place(relx=0.5, rely=0.5, anchor="center")

        identity = ctk.CTkFrame(top_row, fg_color="transparent")
        identity.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._device_name_label = _label(identity, "Spigen P10", font=FONT_BOLD)
        self._device_name_label.pack(anchor="w")
        self._device_subtitle = _label(identity, "Searching BLE...", font=FONT_TINY, fg=FG_SUBTLE)
        self._device_subtitle.pack(anchor="w")

        # Battery Bar inside Card
        self._battery_frame = ctk.CTkFrame(dev_card, fg_color="transparent")
        self._battery_frame.pack(fill="x", padx=12, pady=(6, 2))
        self._battery_frame.pack_forget()

        bat_row = ctk.CTkFrame(self._battery_frame, fg_color="transparent")
        bat_row.pack(fill="x")
        _label(bat_row, "BATTERY", font=FONT_TINY, fg=FG_TINY).pack(side="left")
        self._battery_label = _label(bat_row, "—", font=FONT_BOLD, fg=FG_BATTERY, anchor="e")
        self._battery_label.pack(side="right")

        self._battery_bar = ctk.CTkProgressBar(self._battery_frame, height=5,
                                               progress_color=FG_CONNECTED, fg_color="#181c2c")
        self._battery_bar.set(0)
        self._battery_bar.pack(fill="x", pady=(3, 0))

        self._firmware_label = _label(dev_card, "", font=FONT_TINY, fg=FG_TINY)
        self._firmware_label.pack(anchor="w", padx=12, pady=(2, 8))
        self._firmware_label.pack_forget()

        # ── Device Pairing Button ─────────────────────────────────────
        pick_section = ctk.CTkFrame(sb, fg_color="transparent")
        pick_section.pack(fill="x", padx=14, pady=(12, 0))

        self._scan_btn = ctk.CTkButton(
            pick_section,
            text="🔍  Scan & Pair Device",
            font=FONT_SMALL,
            fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
            text_color=FG_ACCENT,
            border_width=1, border_color=BORDER_CARD,
            corner_radius=8, height=32,
            command=self._on_scan_clicked,
        )
        self._scan_btn.pack(fill="x")

        saved_addr = self._load_setting("target_address", "")
        saved_label = self._load_setting("target_label", "")
        if saved_addr:
            self.service.target_address = saved_addr
            pinned_text = f"📌 {saved_label or saved_addr}"
        else:
            pinned_text = "Auto-detect (Spigen keyword)"
        self._pinned_label = _label(pick_section, pinned_text, font=FONT_TINY, fg=FG_TINY)
        self._pinned_label.pack(anchor="w", pady=(3, 0))

        # ── Navigation Deck ───────────────────────────────────────────
        _label(sb, "CONTROL HUBS", font=FONT_EYEBROW, fg="#64748b").pack(anchor="w", padx=16, pady=(16, 4))

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for icon, title, page_name in (
            ("◉", "Noise Control", "anc"),
            ("⌁", "Equalizer", "eq"),
            ("⌨", "Button Mapping", "buttons"),
        ):
            btn = ctk.CTkButton(
                sb, text=f"  {icon}   {title}", font=FONT_MAIN,
                anchor="w", fg_color="transparent",
                text_color=FG_SUBTLE, hover_color=BG_CARD_HOVER,
                corner_radius=8, height=38,
                command=lambda p=page_name: self._show_page(p),
            )
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_buttons[page_name] = btn

        # ── Quick Hub Toggles ─────────────────────────────────────────
        _label(sb, "PREFERENCES", font=FONT_EYEBROW, fg="#64748b").pack(anchor="w", padx=16, pady=(16, 4))

        # Start with Windows
        import winreg
        self._startup_var = tk.BooleanVar()
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            winreg.QueryValueEx(k, "SpigenAudioCTRL")
            self._startup_var.set(True)
            winreg.CloseKey(k)
        except OSError:
            self._startup_var.set(False)

        def _on_startup_toggled():
            enable = self._startup_var.get()
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
                if enable:
                    cmd = f'"{sys.executable}" --minimized' if getattr(sys, 'frozen', False) else f'"{sys.executable}" run.py --minimized'
                    winreg.SetValueEx(k, "SpigenAudioCTRL", 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, "SpigenAudioCTRL")
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(k)
            except OSError as e:
                print(f"Failed to set startup key: {e}")

        startup_sw = ctk.CTkSwitch(
            sb, text="Start on Boot", variable=self._startup_var,
            command=_on_startup_toggled, font=FONT_SMALL, text_color=FG_SUBTLE,
            progress_color=FG_ACCENT
        )
        startup_sw.pack(anchor="w", padx=16, pady=(4, 0))

        # Fast Sidebar Gaming Mode Toggle
        gaming_sw = ctk.CTkSwitch(
            sb, text="Gaming Mode (~45ms)", variable=self._gaming_var,
            command=self._on_gaming_toggled, font=FONT_SMALL, text_color=FG_SUBTLE,
            progress_color=FG_GAMING
        )
        gaming_sw.pack(anchor="w", padx=16, pady=(8, 0))

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="both", expand=True)

        _label(sb, "Bluetooth LE 5.3  •  JL DSP", font=FONT_TINY, fg=FG_TINY).pack(padx=14, pady=(0, 6))

        # Connect / Disconnect button
        self._connect_btn = ctk.CTkButton(
            sb, text="Disconnect", font=FONT_BOLD,
            fg_color="transparent", text_color=FG_BATTERY_LOW,
            border_width=1, border_color="#3a2030", hover_color="#201524",
            corner_radius=8, height=36,
            command=self._on_connect_clicked,
        )
        self._connect_btn.pack(fill="x", padx=14, pady=(0, 16))

    # --- Noise Control Page -------------------------------------------
    def _build_anc_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color="transparent", corner_radius=0)
        page.grid_rowconfigure(2, weight=1)
        page.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 0))
        _label(hdr, "ACOUSTIC ENGINE", font=FONT_EYEBROW, fg=FG_ACCENT).pack(anchor="w")
        _label(hdr, "Noise Control", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Select high-performance acoustic isolation or ambient awareness profile",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        # Quick Master Mode Bar (ANC On | Transparency | Off)
        quick_bar = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=10, border_width=1, border_color=BORDER_CARD)
        quick_bar.grid(row=1, column=0, sticky="ew", padx=28, pady=(14, 0))

        _label(quick_bar, "QUICK MODES:", font=FONT_EYEBROW, fg=FG_TINY).pack(side="left", padx=(16, 10), pady=10)

        ctk.CTkButton(
            quick_bar, text="◉ Active ANC", font=FONT_BOLD,
            fg_color="#271c3d", hover_color="#352654", text_color="#c084fc",
            corner_radius=6, height=30, width=120,
            command=lambda: self._select_anc_by_id(66816)
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            quick_bar, text="◎ Transparency", font=FONT_BOLD,
            fg_color="#182733", hover_color="#213545", text_color=FG_CYAN,
            corner_radius=6, height=30, width=130,
            command=lambda: self._select_anc_by_id(196868)
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            quick_bar, text="○ Passive Off", font=FONT_BOLD,
            fg_color="#1f2430", hover_color="#2b3242", text_color=FG_SUBTLE,
            corner_radius=6, height=30, width=110,
            command=lambda: self._select_anc_by_id(131072)
        ).pack(side="left", padx=4)

        # Detailed 6-card grid (3x2)
        grid_frame = ctk.CTkFrame(page, fg_color="transparent")
        grid_frame.grid(row=2, column=0, sticky="nsew", padx=28, pady=(14, 0))
        for col in range(3):
            grid_frame.grid_columnconfigure(col, weight=1)
        for r in range(2):
            grid_frame.grid_rowconfigure(r, weight=1)

        self._anc_buttons: list[ctk.CTkFrame] = []
        for i, (name, tag, desc, icon, anc_id) in enumerate(ANC_MODES):
            tile = self._make_anc_tile(grid_frame, name, tag, desc, icon, anc_id)
            tile.grid(row=i // 3, column=i % 3, padx=6, pady=6, sticky="nsew")
            self._anc_buttons.append(tile)

        # Gaming mode banner card
        gaming = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=12,
                              border_width=1, border_color=BORDER_CARD)
        gaming.grid(row=3, column=0, sticky="ew", padx=28, pady=(12, 22))

        icon_frame = ctk.CTkFrame(gaming, fg_color="#301524", corner_radius=8, width=38, height=38)
        icon_frame.pack(side="left", padx=14, pady=12)
        icon_frame.pack_propagate(False)
        ctk.CTkLabel(icon_frame, text="⚡", font=FONT_ICON, text_color=FG_GAMING).place(relx=0.5, rely=0.5, anchor="center")

        gaming_text = ctk.CTkFrame(gaming, fg_color="transparent")
        gaming_text.pack(side="left", fill="x", expand=True)
        _label(gaming_text, "Low-Latency Gaming Mode", font=FONT_BOLD).pack(anchor="w")
        _label(gaming_text, "Reduces wireless Bluetooth transmission delay to ~45 ms for competitive sync",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        ctk.CTkSwitch(
            gaming, text="", variable=self._gaming_var,
            progress_color=FG_GAMING, button_color=FG_TEXT,
            command=self._on_gaming_toggled,
        ).pack(side="right", padx=16)

        return page

    def _make_anc_tile(self, parent, name, tag, desc, icon, anc_id) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12,
                            border_width=1, border_color=BORDER_CARD, cursor="hand2")
        tile._anc_id = anc_id  # type: ignore[attr-defined]

        top = ctk.CTkFrame(tile, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 4))

        icon_frame = ctk.CTkFrame(top, fg_color="#201a35", corner_radius=999, width=32, height=32)
        icon_frame.pack(side="left")
        icon_frame.pack_propagate(False)
        icon_lbl = ctk.CTkLabel(icon_frame, text=icon, font=("Segoe UI", 14), text_color=FG_ACCENT)
        icon_lbl.place(relx=0.5, rely=0.5, anchor="center")
        tile._icon_frame = icon_frame  # type: ignore[attr-defined]

        name_lbl = _label(top, name, font=FONT_BOLD)
        name_lbl.pack(side="left", padx=(8, 0), fill="x", expand=True)

        tag_lbl = ctk.CTkLabel(top, text=tag, font=FONT_TINY, text_color="#c4b5fd",
                               fg_color="#2e2246", corner_radius=6, padx=8, pady=2)
        tag_lbl.pack(side="right")

        desc_lbl = ctk.CTkLabel(tile, text=desc, font=FONT_SMALL, text_color=FG_SUBTLE,
                                wraplength=200, anchor="w", justify="left")
        desc_lbl.pack(anchor="w", padx=12, pady=(2, 12))

        def _click(_e=None, _t=tile, _aid=anc_id):
            self._select_anc(_t, _aid)

        for w in (tile, top, name_lbl, desc_lbl, icon_lbl, icon_frame, tag_lbl):
            w.bind("<Button-1>", _click)

        return tile

    def _select_anc_by_id(self, anc_id: int) -> None:
        target_tile = None
        for tile in self._anc_buttons:
            if getattr(tile, "_anc_id", None) == anc_id:
                target_tile = tile
                break
        if target_tile:
            self._select_anc(target_tile, anc_id)
        else:
            self._anc_var.set(anc_id)
            if self.service.connected:
                self.service.set_anc_mode(anc_id)

    def _select_anc(self, selected_tile: ctk.CTkFrame, anc_id: int) -> None:
        for tile in self._anc_buttons:
            is_sel = (tile is selected_tile)
            tile.configure(
                fg_color=BG_CARD_ACTIVE if is_sel else BG_CARD,
                border_color=BORDER_ACTIVE if is_sel else BORDER_CARD,
            )
            tile._icon_frame.configure(fg_color=FG_ACCENT if is_sel else "#201a35")
        self._anc_var.set(anc_id)
        for name, _tag, _desc, _icon, aid in ANC_MODES:
            if aid == anc_id:
                self._chip_profile.configure(text=name)
                break
        if self.service.connected:
            self.service.set_anc_mode(anc_id)

    # --- Equalizer Page -----------------------------------------------
    def _build_eq_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color="transparent", corner_radius=0)
        page.grid_rowconfigure(2, weight=1)
        page.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 0))
        _label(hdr, "STUDIO DSP ENGINE", font=FONT_EYEBROW, fg=FG_ACCENT).pack(anchor="w")
        _label(hdr, "10-Band Hardware Equalizer", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Fine-tune 16-bit parametric filters stored in headphone DSP memory",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        # Quick Preset Chips Deck
        preset_chips = ctk.CTkFrame(hdr, fg_color="transparent")
        preset_chips.pack(anchor="w", pady=(10, 0))

        chip_list = [
            ("Harman Audiophile", "Harman Audiophile Target"),
            ("Bass Boost", "Bass Boost"),
            ("Pop", "Pop"),
            ("Rock", "Rock"),
            ("Vocal", "Vocal Enhance"),
            ("Gaming", "Gaming Footstep & Spatial"),
            ("Flat", "Default Flat"),
        ]
        for label, preset_key in chip_list:
            ctk.CTkButton(
                preset_chips, text=label, font=FONT_TINY,
                fg_color=BG_CARD, hover_color=BG_CARD_HOVER, text_color=FG_TEXT,
                border_width=1, border_color=BORDER_CARD,
                corner_radius=6, height=26,
                command=lambda k=preset_key: self._apply_preset(k)
            ).pack(side="left", padx=(0, 5))

        # Dropdown + Actions row
        ctrl_row = ctk.CTkFrame(hdr, fg_color="transparent")
        ctrl_row.pack(anchor="w", pady=(8, 0))

        preset_names = list(self.presets.keys())
        self._preset_var = tk.StringVar(value=preset_names[0])
        self._preset_combo = ctk.CTkOptionMenu(
            ctrl_row, variable=self._preset_var,
            values=preset_names, width=220,
            fg_color=BG_CARD, button_color=FG_ACCENT, button_hover_color=FG_ACCENT_HOVER,
            dropdown_fg_color=BG_CARD,
            command=self._on_preset_changed,
        )
        self._preset_combo.pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl_row, text="💾 Save Preset", font=FONT_BOLD,
                      fg_color=FG_ACCENT, hover_color=FG_ACCENT_HOVER,
                      corner_radius=8, height=32, width=120,
                      command=self._save_preset).pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl_row, text="↺ Zero Flat", font=FONT_MAIN,
                      fg_color=BG_CARD, hover_color=BG_CARD_HOVER,
                      corner_radius=8, height=32, width=100,
                      command=lambda: self._apply_preset("Default Flat")).pack(side="left")

        self._preset_status = _label(hdr, "", font=FONT_TINY, fg=FG_TINY)
        self._preset_status.pack(anchor="w", pady=(2, 0))

        # EQ Graph
        graph_wrap = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=12,
                                  border_width=1, border_color=BORDER_CARD)
        graph_wrap.grid(row=1, column=0, sticky="ew", padx=28, pady=(10, 0))

        self._eq_graph = EQGraph(graph_wrap, self.gains)
        self._eq_graph.pack(fill="both", expand=True, padx=2, pady=2)
        self._eq_graph.on_gain_change = self._on_graph_gain

        # Slider Rack
        rack = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=12,
                            border_width=1, border_color=BORDER_CARD)
        rack.grid(row=2, column=0, sticky="nsew", padx=28, pady=(8, 0))

        self._gain_value_labels: list[ctk.CTkLabel] = []
        self._sliders: list[ctk.CTkSlider] = []

        for i, freq in enumerate(EQ_FREQUENCIES):
            col = ctk.CTkFrame(rack, fg_color="transparent")
            col.pack(side="left", fill="both", expand=True, padx=2, pady=8)

            val_lbl = _label(col, "0dB", font=FONT_TINY, fg=FG_TEXT, anchor="center")
            val_lbl.pack()
            self._gain_value_labels.append(val_lbl)

            slider = ctk.CTkSlider(
                col, from_=8, to=-8, variable=self._slider_vars[i],
                orientation="vertical", height=115,
                progress_color=FG_ACCENT, button_color=FG_TEXT,
                command=lambda v, idx=i: self._on_slider_changed(idx, v),
            )
            slider.pack(fill="y", expand=True)
            self._sliders.append(slider)

            freq_text = f"{freq // 1000}k" if freq >= 1000 else str(freq)
            _label(col, freq_text, font=FONT_TINY, fg=FG_SUBTLE, anchor="center").pack()

        # Harman reference checkbox
        harman_frame = ctk.CTkFrame(page, fg_color="transparent")
        harman_frame.grid(row=3, column=0, sticky="w", padx=28, pady=(6, 18))
        self._harman_check = ctk.CTkCheckBox(
            harman_frame, text="Display Harman Target 2019 reference curve",
            variable=self._show_harman_var, font=FONT_SMALL,
            checkmark_color=BG_MAIN, fg_color=FG_WARNING,
            command=self._on_harman_toggled,
        )
        self._harman_check.pack(anchor="w")

        self._apply_preset("Harman Audiophile Target", send=False)
        return page

    # --- Button Mapping Page ------------------------------------------
    def _build_buttons_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color="transparent", corner_radius=0)
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 0))
        _label(hdr, "HARDWARE CONTROLS", font=FONT_EYEBROW, fg=FG_ACCENT).pack(anchor="w")
        _label(hdr, "Button Remapping", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Customize tactile button actions and gestures written directly to on-board memory",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        card = ctk.CTkScrollableFrame(page, fg_color=BG_CARD, corner_radius=12,
                                      border_width=1, border_color=BORDER_CARD)
        card.grid(row=1, column=0, sticky="nsew", padx=28, pady=(14, 22))

        fn_names = [fname for _, fname in FUNCTIONS]
        fn_ids = [str(fid) for fid, _ in FUNCTIONS]

        for row in REMAP_ROWS:
            key = f"{row.key_number}_{row.action}"
            var = tk.StringVar(value=str(row.default_function))
            self._remap_vars[key] = var

            line = ctk.CTkFrame(card, fg_color="transparent", height=50)
            line.pack(fill="x", padx=6, pady=2)
            line.pack_propagate(False)

            badge = ctk.CTkLabel(line, text=row.badge, font=FONT_BADGE,
                                 text_color=FG_ACCENT, fg_color="#241b3d",
                                 corner_radius=6, width=36, height=26)
            badge.pack(side="left", padx=(10, 0), pady=12)

            _label(line, row.key_name, font=FONT_BOLD).pack(side="left", padx=(10, 0))
            _label(line, f"({row.gesture})", font=FONT_SMALL, fg=FG_SUBTLE).pack(side="left", padx=(8, 0))

            combo = ctk.CTkOptionMenu(
                line, variable=var, values=fn_names, width=190,
                fg_color=BG_MAIN, button_color=FG_ACCENT, button_hover_color=FG_ACCENT_HOVER,
                dropdown_fg_color=BG_CARD,
                command=lambda _val, _r=row, _var=var, _ids=fn_ids, _names=fn_names:
                    self._on_remap_changed(_r, _var, _ids, _names),
            )
            combo.pack(side="right", padx=(0, 10))

            default_name = FUNCTION_NAMES.get(row.default_function, "Play / Pause")
            var.set(default_name)
            _sep(card).pack(fill="x", padx=6)

        return page

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _show_page(self, name: str) -> None:
        self._current_page = name
        for pname, page in self._pages.items():
            if pname == name:
                page.tkraise()
        for pname, btn in self._nav_buttons.items():
            if pname == name:
                btn.configure(fg_color="#271c3d", text_color=FG_TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=FG_SUBTLE)
        if name == "eq":
            self.after(10, self._eq_graph.redraw)

    # ------------------------------------------------------------------
    # BLE Callbacks
    # ------------------------------------------------------------------
    def _on_connection(self, connected: bool, status: str) -> None:
        if connected:
            self._chip_status.configure(text="● Online", text_color=FG_CONNECTED)
            self._device_name_label.configure(text=status)
            self._device_subtitle.configure(text="Connected • BLE 5.3")
            self._connect_btn.configure(
                text="Disconnect",
                text_color=FG_BATTERY_LOW,
                border_color="#3a2030",
                hover_color="#201524",
            )
        else:
            connecting = status.startswith("Connecting")
            failed = status.startswith("Error:")
            st_text = "● Connecting…" if connecting else ("● Failed" if failed else "● Offline")
            self._chip_status.configure(
                text=st_text,
                text_color=FG_SUBTLE if connecting else FG_DISCONNECTED
            )
            self._device_subtitle.configure(
                text="Connecting…" if connecting else ("No device attached" if not failed else "Connection Error")
            )
            self._connect_btn.configure(
                text="Connecting…" if connecting else ("Try Again" if failed else "Pair Headphones"),
                text_color=FG_ACCENT if not connecting else FG_SUBTLE,
                border_color=BORDER_CARD,
                hover_color=BG_CARD_HOVER,
            )
            self._battery_frame.pack_forget()
            self._firmware_label.pack_forget()
            self._chip_battery.configure(text="— %", text_color=FG_SUBTLE)

        self._update_tray_title()

    def _on_battery(self, level: int) -> None:
        color = FG_BATTERY if level > 20 else FG_BATTERY_LOW
        self._battery_label.configure(text=f"{level}%", text_color=color)
        self._chip_battery.configure(text=f"{level}%", text_color=color)
        self._battery_bar.set(level / 100)
        self._battery_bar.configure(progress_color=color)
        self._battery_frame.pack(fill="x", padx=12, pady=(6, 2))
        self._update_tray_title()

    def _on_hardware_info(self, info: HardwareInfo) -> None:
        self._updating_hardware = True
        try:
            if info.device_name:
                self._device_name_label.configure(text=info.device_name)
            if info.firmware:
                self._firmware_label.configure(text=f"Firmware: {info.firmware}")
                self._firmware_label.pack(anchor="w", padx=12, pady=(2, 8))
            if info.gaming_mode is not None:
                self._gaming_var.set(info.gaming_mode)
                self._chip_gaming.configure(
                    text="45ms Gaming Sync" if info.gaming_mode else "Music Mode (Standard)",
                    text_color=FG_GAMING if info.gaming_mode else FG_SUBTLE
                )
            for key, function in info.key_settings.items():
                var = self._remap_vars.get(key)
                if var:
                    fn_val = 0 if function == 127 else function
                    var.set(FUNCTION_NAMES.get(fn_val, "Disabled"))
        finally:
            self._updating_hardware = False

    # ------------------------------------------------------------------
    # User Actions
    # ------------------------------------------------------------------
    def _on_connect_clicked(self) -> None:
        if self.service.connected:
            self.service.disconnect()
        else:
            self.service.connect()

    def _on_scan_clicked(self) -> None:
        if self.service.connected:
            self.service.disconnect()
        self._scan_btn.configure(text="⏳  Scanning…", state="disabled")
        self.service.on_scan_result = self._on_scan_result
        self.service.scan_all_devices()

    def _on_scan_result(self, devices: list[ScannedDevice]) -> None:
        self._scan_btn.configure(text="🔍  Scan & Pair Device", state="normal")
        self.service.on_scan_result = None
        if not devices:
            from tkinter import messagebox
            messagebox.showwarning(
                "No Devices Found",
                "No Bluetooth LE devices were discovered.\n\n"
                "• Check that your headphones are powered on and nearby.\n"
                "• Ensure Bluetooth is enabled in Windows.",
                parent=self,
            )
            return
        DevicePickerDialog(self, devices, self._on_device_picked)

    def _on_device_picked(self, device: ScannedDevice) -> None:
        self.service.target_address = device.address
        label = device.name
        self._pinned_label.configure(text=f"📌 {label}")
        self._save_setting("target_address", device.address)
        self._save_setting("target_label", label)
        self.service.connect()

    @property
    def _settings_path(self) -> Path:
        return (
            Path(os.getenv("APPDATA") or os.path.expanduser("~"))
            / "SpigenAudioCTRL"
            / "settings.json"
        )

    def _load_setting(self, key: str, default: str = "") -> str:
        try:
            doc = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return str(doc.get(key, default))
        except (OSError, ValueError, TypeError):
            return default

    def _save_setting(self, key: str, value: str) -> None:
        try:
            path = self._settings_path
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = {}
            doc[key] = value
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def _on_gaming_toggled(self) -> None:
        enabled = self._gaming_var.get()
        self._chip_gaming.configure(
            text="45ms Gaming Sync" if enabled else "Music Mode (Standard)",
            text_color=FG_GAMING if enabled else FG_SUBTLE
        )
        if not self._updating_hardware and self.service.connected:
            self.service.set_gaming_mode(enabled)

    def _on_slider_changed(self, index: int, value: float) -> None:
        gain = round(value * 2) / 2
        if abs(gain) < 1e-6:
            gain = 0.0
        self.gains[index] = gain
        sign = "+" if gain > 0 else ""
        self._gain_value_labels[index].configure(text=f"{sign}{gain:g}dB")
        self._eq_graph.gains = self.gains
        self._eq_graph.redraw()
        self._debounce_eq()

    def _on_graph_gain(self, index: int, value: float) -> None:
        self._slider_vars[index].set(value)
        self._on_slider_changed(index, value)

    def _debounce_eq(self) -> None:
        if self._eq_after_id is not None:
            self.after_cancel(self._eq_after_id)
        self._eq_after_id = self.after(150, self._send_eq)

    def _send_eq(self) -> None:
        self._eq_after_id = None
        if self.service.connected:
            self.service.set_equalizer(self.gains.copy())

    def _on_preset_changed(self, name: str) -> None:
        self._apply_preset(name)

    def _apply_preset(self, name: str, send: bool = True) -> None:
        gains = self.presets.get(name)
        if gains is None:
            return
        for i, gain in enumerate(gains):
            g = 0.0 if abs(gain) < 1e-6 else float(gain)
            self.gains[i] = g
            self._slider_vars[i].set(g)
            sign = "+" if g > 0 else ""
            self._gain_value_labels[i].configure(text=f"{sign}{g:g}dB")
        self._eq_graph.gains = self.gains
        self._eq_graph.redraw()
        if send:
            self._debounce_eq()

    def _save_preset(self) -> None:
        number = 1
        while f"Custom {number}" in self.presets:
            number += 1
        name = simpledialog.askstring(
            "Save Preset",
            "Give this custom sound profile a name:",
            initialvalue=f"Custom {number}",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = self._unique_custom_name(name.strip())
        self.custom_presets[name] = self.gains.copy()
        self.presets[name] = self.gains.copy()
        self._preset_combo.configure(values=list(self.presets.keys()))
        self._preset_var.set(name)
        try:
            self._persist_custom_presets()
            self._preset_status.configure(text=f'Saved "{name}" to Task Hub')
        except OSError as err:
            self._preset_status.configure(text=f"Could not save: {err}")

    def _unique_custom_name(self, requested: str) -> str:
        if requested not in self.presets:
            return requested
        n = 2
        while f"{requested} {n}" in self.presets:
            n += 1
        return f"{requested} {n}"

    def _load_custom_presets(self) -> None:
        try:
            doc = json.loads(self._presets_path.read_text(encoding="utf-8"))
            saved = doc.get("presets", {}) if isinstance(doc, dict) else {}
            for name, gains in saved.items():
                if (
                    isinstance(name, str) and name.strip()
                    and isinstance(gains, list) and len(gains) == 10
                    and all(isinstance(g, (int, float)) and -8 <= g <= 8 for g in gains)
                ):
                    clean = [float(g) for g in gains]
                    cname = self._unique_custom_name(name.strip())
                    self.custom_presets[cname] = clean
                    self.presets[cname] = clean.copy()
        except (OSError, ValueError, TypeError):
            pass

    def _persist_custom_presets(self) -> None:
        self._presets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._presets_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "presets": self.custom_presets}, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._presets_path)

    def _on_remap_changed(self, row: RemapRow, var: tk.StringVar,
                          fn_ids: list[str], fn_names: list[str]) -> None:
        if self._updating_hardware or not self.service.connected:
            return
        name = var.get()
        try:
            idx = fn_names.index(name)
            function_id = int(fn_ids[idx])
        except (ValueError, IndexError):
            return
        self.service.set_key_mapping(row.key_number, row.action, function_id)

    def _on_harman_toggled(self) -> None:
        self._eq_graph.show_harman = self._show_harman_var.get()
        self._eq_graph.redraw()

    # ------------------------------------------------------------------
    # Window Lifecycle & System Tray
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if _TRAY_AVAILABLE:
            self._minimize_to_tray()
        else:
            self._do_quit()

    def _minimize_to_tray(self) -> None:
        self.withdraw()
        if self._tray is None:
            self._start_tray()

    def _start_tray(self) -> None:
        try:
            icon_path = _resource_path("assets/icon.png")
            if icon_path.exists():
                img = PILImage.open(str(icon_path)).convert("RGBA")
            else:
                img = PILImage.new("RGBA", (64, 64), "#a855f7")

            def _show(_icon=None, _item=None):
                self._tray_restore()

            def _toggle(_icon=None, _item=None):
                if self.service.connected:
                    self.service.disconnect()
                else:
                    self.service.connect()

            def _quit(_icon=None, _item=None):
                self._tray_quit()

            def _set_anc_tray(mode: int):
                def _handler(_i=None, _item=None):
                    self.after(0, lambda: self._select_anc_by_id(mode))
                return _handler

            def _menu_generator():
                status = "Disconnected"
                if self.service.connected:
                    bat = self.service.battery_level
                    status = f"Connected ({bat}%)" if bat is not None else "Connected"

                yield pystray.MenuItem("Show Window", _show, default=True)
                yield pystray.MenuItem(f"Status: {status}", lambda *_: None, enabled=False)
                yield pystray.Menu.SEPARATOR

                # Correct 32-bit JL ANC IDs
                if self.service.connected:
                    yield pystray.MenuItem("ANC: Adaptive", _set_anc_tray(66816),
                                           checked=lambda i: self._anc_var.get() == 66816, radio=True)
                    yield pystray.MenuItem("ANC: Deep (-43dB)", _set_anc_tray(65794),
                                           checked=lambda i: self._anc_var.get() == 65794, radio=True)
                    yield pystray.MenuItem("ANC: Transparency", _set_anc_tray(196868),
                                           checked=lambda i: self._anc_var.get() == 196868, radio=True)
                    yield pystray.MenuItem("ANC: Off", _set_anc_tray(131072),
                                           checked=lambda i: self._anc_var.get() == 131072, radio=True)
                    yield pystray.Menu.SEPARATOR

                action = "Disconnect" if self.service.connected else "Connect"
                yield pystray.MenuItem(action, _toggle)
                yield pystray.Menu.SEPARATOR
                yield pystray.MenuItem("Exit Spigen Hub", _quit)

            menu = pystray.Menu(_menu_generator)
            self._tray = pystray.Icon("SpigenAudioCTRL", img, "Spigen Audio Hub", menu)
            self._update_tray_title()
            self._tray_thread = threading.Thread(target=self._tray.run, daemon=True, name="tray")
            self._tray_thread.start()

        except Exception as exc:
            print(f"[TRAY] Error: {exc}")
            self.deiconify()

    def _update_tray_title(self) -> None:
        if self._tray:
            if self.service.connected:
                bat = self.service.battery_level
                b_str = f" ({bat}%)" if bat is not None else ""
                self._tray.title = f"Spigen Audio Hub\nConnected{b_str}"
            else:
                self._tray.title = "Spigen Audio Hub\nDisconnected"
            try:
                self._tray.update_menu()
            except Exception:
                pass

    def _tray_restore(self) -> None:
        self.after(0, self._restore_window)

    def _restore_window(self) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.focus_force()

    def _tray_quit(self) -> None:
        self.after(0, self._do_quit)

    def _do_quit(self) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        if self._eq_after_id is not None:
            self.after_cancel(self._eq_after_id)
        self.service.close()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main() -> int:
    app = MainWindow()
    if "--minimized" in sys.argv and _TRAY_AVAILABLE:
        app._minimize_to_tray()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
