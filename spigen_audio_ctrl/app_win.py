"""customtkinter desktop interface for Spigen Audio CTRL — Windows port.

Faithful recreation of the Linux GTK 4 app using customtkinter and a
tkinter.Canvas EQ graph. All features are preserved:
  - Six ANC profiles
  - Low-latency Gaming Mode toggle
  - 10-band hardware DSP EQ with live graph, Harman target overlay, presets
  - Button remapping for MFB and volume keys
  - Battery level display
  - BLE connect / disconnect / auto-reconnect
"""

from __future__ import annotations

import json
import math
import os
import sys
import tkinter as tk
import tkinter.simpledialog as simpledialog
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from .bluetooth_win import BluetoothService, ScannedDevice
from .protocol import EQ_FREQUENCIES, HardwareInfo

# ---------------------------------------------------------------------------
# Theme / colour palette (mirrors the original dark palette)
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_MAIN = "#0b0b12"
BG_SIDEBAR = "#0e0e18"
BG_CARD = "#13131d"
BG_DEVICE_CARD_TOP = "#24203a"
BG_DEVICE_CARD_BOT = "#151522"
BG_ANC_TILE = "#13131d"
BG_ANC_TILE_HOVER = "#181824"
BG_ANC_SELECTED = "#211c39"
BG_GAMING_CARD = "#171525"

FG_TEXT = "#f7f5ff"
FG_SUBTLE = "#9a97aa"
FG_TINY = "#777486"
FG_EYEBROW = "#b7a0ff"
FG_BATTERY = "#84edbd"
FG_BATTERY_LOW = "#ff8c75"
FG_CONNECTED = "#72e6b1"
FG_DISCONNECTED = "#ff8c75"
FG_MARK = "#ff7759"
FG_ANC_ICON = "#c5b3ff"
FG_ACCENT = "#9c7cff"
FG_BADGE = "#c5b3ff"
FG_GAMING = "#ff896f"

BORDER_CARD = "#1e1e2e"
BORDER_ANC = "#1e1e2e"
BORDER_ANC_SEL = "#9c7cff"
BORDER_DEVICE = "#3a2e55"

FONT_MAIN = ("Segoe UI", 13)
FONT_BOLD = ("Segoe UI", 13, "bold")
FONT_SMALL = ("Segoe UI", 11)
FONT_TINY = ("Segoe UI", 10)
FONT_HEADING = ("Segoe UI", 22, "bold")
FONT_EYEBROW = ("Segoe UI", 10, "bold")
FONT_MARK = ("Segoe UI", 18, "bold")
FONT_BADGE = ("Segoe UI", 11, "bold")
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
    ("Deep ANC", "-43 dB", "Maximum isolation for flights, transit, and loud environments.", "◉", 65794),
    ("Adaptive ANC", "Dynamic", "Adapts filter strength to your surroundings and current ear seal.", "✦", 66816),
    ("Commuting", "Transit", "Targets low-frequency vehicle rumble on trains and buses.", "≋", 66048),
    ("Anti-Wind", "Outdoor", "Suppresses turbulence across the external microphones.", "〰", 66560),
    ("Transparency", "Ambient", "Stay aware of conversations and the world around you.", "◎", 196868),
    ("Off", "Passive", "Turns off ANC processing for the longest possible battery life.", "○", 131072),
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
# Helper widgets
# ---------------------------------------------------------------------------

def _sep(parent: Any, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER_CARD, **kw)


def _label(parent: Any, text: str, font=FONT_MAIN, fg: str = FG_TEXT,
           anchor: str = "w", **kw) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text, font=font, text_color=fg,
                        anchor=anchor, **kw)


# ---------------------------------------------------------------------------
# EQ graph (pure tkinter Canvas — replicates the Cairo drawing)
# ---------------------------------------------------------------------------

class EQGraph(tk.Canvas):
    PAD_X, PAD_Y = 36, 22

    def __init__(self, parent: Any, gains: list[float], **kw) -> None:
        super().__init__(
            parent,
            bg=BG_CARD,
            bd=0, highlightthickness=0,
            height=165,
            **kw,
        )
        self.gains = gains
        self.show_harman = True
        self._drag_band = -1
        self._drag_start_y = 0.0
        self._drag_start_gain = 0.0
        self.on_gain_change: Any = None  # callback(index, value)

        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)

    def _node(self, index: int, gain: float) -> tuple[float, float]:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            w, h = 600, 165
        x = self.PAD_X + index / 9 * max(1, w - self.PAD_X * 2)
        y = self.PAD_Y + (8 - gain) / 16 * max(1, h - self.PAD_Y * 2)
        return x, y

    def _curve_points(self, gains: list[float]) -> list[tuple[float, float]]:
        return [self._node(i, g) for i, g in enumerate(gains)]

    def _bezier_path(self, pts: list[tuple[float, float]]) -> list[float]:
        """Convert control points to a dense polyline via Catmull-Rom spline."""
        result: list[float] = []
        for i in range(len(pts) - 1):
            p0 = pts[max(0, i - 1)]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[min(len(pts) - 1, i + 2)]
            for t_step in range(20):
                t = t_step / 20
                # Catmull-Rom
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
        last = pts[-1]
        result += [last[0], last[1]]
        return result

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return

        # Grid lines
        for db in (-8, -4, 0, 4, 8):
            _, y = self._node(0, db)
            alpha = 50 if db == 0 else 20
            colour = f"#{alpha:02x}{alpha:02x}{alpha + 30:02x}"
            if db == 0:
                colour = "#2a2a40"
            self.create_line(27, y, w - 27, y, fill=colour, width=1)
        for i in range(10):
            x, _ = self._node(i, 0)
            self.create_line(x, 16, x, h - 16, fill="#131325", width=1)

        # Harman target overlay
        if self.show_harman:
            pts = self._curve_points(HARMAN_GAINS)
            flat = self._bezier_path(pts)
            if len(flat) >= 4:
                self.create_line(*flat, fill="#e6a83c", width=2, dash=(5, 4))

        # Current curve — filled area
        pts = self._curve_points(self.gains)
        flat = self._bezier_path(pts)
        if len(flat) >= 4:
            x_last, y_last = self._node(9, -8)
            x_first, _ = self._node(0, -8)
            fill_pts = flat + [x_last, y_last, x_first, y_last]
            self.create_polygon(*fill_pts, fill="#1e1e35", outline="")
            # Main curve
            self.create_line(*flat, fill="#ffffff", width=2)

        # Band nodes
        for i, gain in enumerate(self.gains):
            x, y = self._node(i, gain)
            self.create_oval(x - 5.5, y - 5.5, x + 5.5, y + 5.5, fill="#ffffff", outline="")
            self.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill="#0a0a0a", outline="")

    def _band_at(self, x: float) -> int:
        w = self.winfo_width() or 600
        return max(0, min(9, round((x - self.PAD_X) / max(1, w - self.PAD_X * 2) * 9)))

    def _gain_at(self, y: float) -> float:
        h = self.winfo_height() or 165
        value = 8 - (y - self.PAD_Y) / max(1, h - self.PAD_Y * 2) * 16
        return max(-8, min(8, round(value * 2) / 2))

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
        h = self.winfo_height() or 165
        delta = -(event.y - self._drag_start_y) / max(1, h - self.PAD_Y * 2) * 16
        new_gain = max(-8, min(8, round((self._drag_start_gain + delta) * 2) / 2))
        if self.on_gain_change:
            self.on_gain_change(self._drag_band, new_gain)


# ---------------------------------------------------------------------------
# Device picker dialog
# ---------------------------------------------------------------------------

class DevicePickerDialog(ctk.CTkToplevel):
    """Modal dialog showing all scanned BLE devices; user clicks one to select."""

    def __init__(self, parent, devices: "list[ScannedDevice]",
                 on_picked: "Callable[[ScannedDevice], None]") -> None:
        super().__init__(parent)
        self.title("Select Your Headphones")
        self.geometry("480x440")
        self.resizable(False, False)
        self.configure(fg_color=BG_MAIN)
        self.grab_set()          # modal
        self.lift()
        self.focus_force()

        self._on_picked = on_picked
        self._devices = devices

        _label(self, "Choose your headphones from the list below.",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(padx=20, pady=(18, 6), anchor="w")
        _label(self, f"{len(devices)} device(s) found  •  sorted by signal strength",
               font=FONT_TINY, fg=FG_TINY).pack(padx=20, anchor="w")

        scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_CARD, corner_radius=12,
            border_width=1, border_color=BORDER_CARD,
        )
        scroll.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        for dev in devices:
            self._make_row(scroll, dev)

        ctk.CTkButton(
            self, text="Cancel", font=FONT_SMALL,
            fg_color="transparent", text_color=FG_SUBTLE,
            hover_color=BG_CARD, corner_radius=8, height=34,
            command=self.destroy,
        ).pack(pady=(10, 16))

    @staticmethod
    def _rssi_bar(rssi: int) -> str:
        """Convert RSSI dBm to a simple signal strength bar."""
        if rssi >= -60:  return "▰▰▰▰▰"
        if rssi >= -70:  return "▰▰▰▰▱"
        if rssi >= -80:  return "▰▰▰▱▱"
        if rssi >= -90:  return "▰▰▱▱▱"
        return              "▰▱▱▱▱"

    @staticmethod
    def _rssi_color(rssi: int) -> str:
        if rssi >= -65:  return FG_CONNECTED
        if rssi >= -80:  return "#ffb552"
        return FG_DISCONNECTED

    def _make_row(self, parent, dev: "ScannedDevice") -> None:
        row = ctk.CTkFrame(
            parent, fg_color="transparent", cursor="hand2", height=54
        )
        row.pack(fill="x", padx=4, pady=2)
        row.pack_propagate(False)

        # Device icon
        icon = ctk.CTkLabel(row, text="🎧", font=("Segoe UI Emoji", 20),
                            width=36)
        icon.pack(side="left", padx=(8, 0))

        # Name + address
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(8, 0))
        name_lbl = _label(info, dev.name, font=FONT_BOLD, fg=FG_TEXT)
        name_lbl.pack(anchor="w")
        addr_lbl = _label(info, dev.address, font=FONT_TINY, fg=FG_TINY)
        addr_lbl.pack(anchor="w")

        # RSSI bar
        bar_text = self._rssi_bar(dev.rssi)
        bar_color = self._rssi_color(dev.rssi)
        bar_lbl = ctk.CTkLabel(row, text=bar_text, font=("Segoe UI", 11),
                               text_color=bar_color, width=60)
        bar_lbl.pack(side="right", padx=(0, 12))

        rssi_lbl = _label(row, f"{dev.rssi} dBm", font=FONT_TINY, fg=FG_TINY, anchor="e")
        rssi_lbl.pack(side="right", padx=(0, 4))

        # Hover highlight + click
        def _enter(_e, _r=row):
            _r.configure(fg_color="#1c1535")
        def _leave(_e, _r=row):
            _r.configure(fg_color="transparent")
        def _click(_e=None, _d=dev):
            self.destroy()
            self._on_picked(_d)

        for widget in (row, icon, name_lbl, addr_lbl, bar_lbl, rssi_lbl, info):
            widget.bind("<Enter>", _enter)
            widget.bind("<Leave>", _leave)
            widget.bind("<Button-1>", _click)

        _sep(parent).pack(fill="x", padx=4)


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class MainWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spigen Audio CTRL")
        self.geometry("1060x700")
        self.minsize(880, 580)
        self.configure(fg_color=BG_MAIN)

        # BLE service (schedule_callback → root.after(0, fn))
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
            Path(os.environ.get("APPDATA", "~")).expanduser()
            / "SpigenAudioCTRL"
            / "presets.json"
        )
        self._load_custom_presets()

        # Per-slider variables
        self._slider_vars: list[tk.DoubleVar] = [tk.DoubleVar(value=0.0) for _ in range(10)]
        self._remap_vars: dict[str, tk.StringVar] = {}
        self._gaming_var = tk.BooleanVar(value=False)
        self._anc_var = tk.IntVar(value=66816)  # Adaptive ANC default
        self._show_harman_var = tk.BooleanVar(value=True)
        self._current_page = "anc"

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Kick off BLE connection
        self.after(200, self.service.connect)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Root layout: sidebar | content
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._content_frame = ctk.CTkFrame(self, fg_color=BG_MAIN, corner_radius=0)
        self._content_frame.grid(row=0, column=1, sticky="nsew")
        self._content_frame.grid_rowconfigure(0, weight=1)
        self._content_frame.grid_columnconfigure(0, weight=1)

        self._pages: dict[str, ctk.CTkFrame] = {}
        self._pages["anc"] = self._build_anc_page()
        self._pages["eq"] = self._build_eq_page()
        self._pages["buttons"] = self._build_buttons_page()

        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self._show_page("anc")

    # --- Sidebar -------------------------------------------------------

    def _build_sidebar(self) -> None:
        sb = ctk.CTkFrame(self, fg_color=BG_SIDEBAR, corner_radius=0, width=258)
        sb.grid(row=0, column=0, sticky="ns")
        sb.grid_propagate(False)

        # ── Device card ──────────────────────────────────────────────
        card = ctk.CTkFrame(sb, fg_color=BG_DEVICE_CARD_TOP, corner_radius=16,
                            border_width=1, border_color=BORDER_DEVICE)
        card.pack(fill="x", padx=14, pady=(20, 0))

        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(14, 4))
        orb = ctk.CTkFrame(top_row, fg_color="#3d1e15", corner_radius=999,
                           width=42, height=42)
        orb.pack(side="left")
        orb.pack_propagate(False)
        ctk.CTkLabel(orb, text="♫", font=FONT_ICON, text_color=FG_GAMING).place(relx=0.5, rely=0.5, anchor="center")

        identity = ctk.CTkFrame(top_row, fg_color="transparent")
        identity.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._device_name_label = _label(identity, "SA-HP P10", font=FONT_BOLD)
        self._device_name_label.pack(anchor="w")
        self._device_subtitle = _label(identity, "Looking for headphones…", font=FONT_SMALL, fg=FG_SUBTLE)
        self._device_subtitle.pack(anchor="w")

        # Battery
        self._battery_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._battery_frame.pack(fill="x", padx=14, pady=(4, 2))
        self._battery_frame.pack_forget()  # hidden until connected

        bat_row = ctk.CTkFrame(self._battery_frame, fg_color="transparent")
        bat_row.pack(fill="x")
        _label(bat_row, "BATTERY", font=FONT_TINY, fg=FG_TINY).pack(side="left")
        self._battery_label = _label(bat_row, "—", font=FONT_BOLD, fg=FG_BATTERY, anchor="e")
        self._battery_label.pack(side="right")
        self._battery_bar = ctk.CTkProgressBar(self._battery_frame, height=6,
                                               progress_color=FG_CONNECTED,
                                               fg_color="#1e1e30")
        self._battery_bar.set(0)
        self._battery_bar.pack(fill="x", pady=(4, 0))

        self._firmware_label = _label(card, "", font=FONT_TINY, fg=FG_TINY)
        self._firmware_label.pack(anchor="w", padx=14, pady=(2, 10))
        self._firmware_label.pack_forget()

        # ── Status pill ───────────────────────────────────────────────
        self._status_frame = ctk.CTkFrame(sb, fg_color="#13131d", corner_radius=999,
                                          border_width=1, border_color="#1f1f30",
                                          height=30)
        self._status_frame.pack(fill="x", padx=14, pady=(12, 0))
        self._status_frame.pack_propagate(False)
        self._status_dot = _label(self._status_frame, "●", font=FONT_SMALL, fg=FG_DISCONNECTED)
        self._status_dot.place(relx=0.14, rely=0.5, anchor="center")
        self._status_text = _label(self._status_frame, "Connecting…", font=FONT_SMALL, fg=FG_SUBTLE)
        self._status_text.place(relx=0.55, rely=0.5, anchor="center")
        # ── Device picker ─────────────────────────────────────────────
        pick_section = ctk.CTkFrame(sb, fg_color="transparent")
        pick_section.pack(fill="x", padx=14, pady=(10, 0))
        _label(pick_section, "DEVICE", font=FONT_EYEBROW, fg="#656274").pack(anchor="w")

        self._scan_btn = ctk.CTkButton(
            pick_section,
            text="🔍  Scan & Pick Device",
            font=FONT_SMALL,
            fg_color=BG_CARD, hover_color="#1c1535",
            text_color=FG_EYEBROW,
            border_width=1, border_color=BORDER_CARD,
            corner_radius=8, height=32,
            command=self._on_scan_clicked,
        )
        self._scan_btn.pack(fill="x", pady=(4, 0))

        # Shows the currently pinned device (address or name)
        saved_addr = self._load_setting("target_address", "")
        saved_label = self._load_setting("target_label", "")
        if saved_addr:
            self.service.target_address = saved_addr
            pinned_text = f"📌 {saved_label or saved_addr}"
        else:
            pinned_text = "Auto-detect (Spigen keyword)"
        self._pinned_label = _label(
            pick_section, pinned_text, font=FONT_TINY, fg=FG_TINY
        )
        self._pinned_label.pack(anchor="w", pady=(3, 0))

        _label(sb, "CONTROL DECK", font=FONT_EYEBROW, fg="#656274").pack(
            anchor="w", padx=14, pady=(18, 4))

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for icon, title, page_name in (
            ("◉", "Noise Control", "anc"),
            ("⌁", "Equalizer", "eq"),
            ("⌨", "Button Mapping", "buttons"),
        ):
            btn = ctk.CTkButton(
                sb, text=f"  {icon}   {title}", font=FONT_MAIN,
                anchor="w", fg_color="transparent",
                text_color=FG_SUBTLE, hover_color="#14141f",
                corner_radius=10, height=40,
                command=lambda p=page_name: self._show_page(p),
            )
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_buttons[page_name] = btn

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="both", expand=True)

        _label(sb, "Bluetooth Low Energy  •  Event driven",
               font=FONT_TINY, fg=FG_TINY).pack(padx=14, pady=(0, 6))

        self._connect_btn = ctk.CTkButton(
            sb, text="Disconnect", font=FONT_BOLD,
            fg_color="transparent", text_color=FG_BATTERY_LOW,
            border_width=1, border_color="#3a2030", hover_color="#1e1020",
            corner_radius=10, height=36,
            command=self._on_connect_clicked,
        )
        self._connect_btn.pack(fill="x", padx=14, pady=(0, 18))

    # --- ANC page ------------------------------------------------------

    def _build_anc_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color=BG_MAIN, corner_radius=0)
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 0))
        _label(hdr, "YOUR SOUND, YOUR RULES", font=FONT_EYEBROW, fg=FG_EYEBROW).pack(anchor="w")
        _label(hdr, "Noise Control", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Select active acoustic isolation or ambient transparency profile",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        # ANC tile grid (3 cols × 2 rows)
        grid_frame = ctk.CTkFrame(page, fg_color="transparent")
        grid_frame.grid(row=1, column=0, sticky="nsew", padx=32, pady=(18, 0))
        for col in range(3):
            grid_frame.grid_columnconfigure(col, weight=1)
        for row in range(2):
            grid_frame.grid_rowconfigure(row, weight=1)

        self._anc_buttons: list[ctk.CTkFrame] = []
        for i, (name, tag, desc, icon, anc_id) in enumerate(ANC_MODES):
            tile = self._make_anc_tile(grid_frame, name, tag, desc, icon, anc_id)
            tile.grid(row=i // 3, column=i % 3, padx=5, pady=5, sticky="nsew")
            self._anc_buttons.append(tile)

        # Gaming mode card
        gaming = ctk.CTkFrame(page, fg_color=BG_GAMING_CARD, corner_radius=14,
                              border_width=1, border_color=BORDER_CARD)
        gaming.grid(row=2, column=0, sticky="ew", padx=32, pady=(14, 26))

        icon_frame = ctk.CTkFrame(gaming, fg_color="#2a1c10", corner_radius=10,
                                  width=42, height=42)
        icon_frame.pack(side="left", padx=14, pady=14)
        icon_frame.pack_propagate(False)
        ctk.CTkLabel(icon_frame, text="⚡", font=FONT_ICON, text_color=FG_GAMING).place(
            relx=0.5, rely=0.5, anchor="center")

        gaming_text = ctk.CTkFrame(gaming, fg_color="transparent")
        gaming_text.pack(side="left", fill="x", expand=True)
        _label(gaming_text, "Low-Latency Gaming Mode", font=FONT_BOLD).pack(anchor="w")
        _label(gaming_text, "Reduces wireless audio transmission delay to ~45 ms",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        self._gaming_switch = ctk.CTkSwitch(
            gaming, text="", variable=self._gaming_var,
            progress_color=FG_ACCENT, button_color=FG_TEXT,
            command=self._on_gaming_toggled,
        )
        self._gaming_switch.pack(side="right", padx=18)
        return page

    def _make_anc_tile(self, parent, name, tag, desc, icon, anc_id) -> ctk.CTkFrame:
        tile = ctk.CTkFrame(parent, fg_color=BG_ANC_TILE, corner_radius=14,
                            border_width=1, border_color=BORDER_ANC,
                            cursor="hand2")
        tile._anc_id = anc_id  # type: ignore[attr-defined]
        tile._selected = False  # type: ignore[attr-defined]

        top = ctk.CTkFrame(tile, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))

        icon_frame = ctk.CTkFrame(top, fg_color="#1e1535", corner_radius=999,
                                  width=34, height=34)
        icon_frame.pack(side="left")
        icon_frame.pack_propagate(False)
        icon_label = ctk.CTkLabel(icon_frame, text=icon, font=("Segoe UI", 15),
                                  text_color=FG_ANC_ICON)
        icon_label.place(relx=0.5, rely=0.5, anchor="center")
        tile._icon_frame = icon_frame  # type: ignore[attr-defined]

        name_lbl = _label(top, name, font=FONT_BOLD)
        name_lbl.pack(side="left", padx=(8, 0), fill="x", expand=True)
        tag_lbl = ctk.CTkLabel(top, text=tag, font=FONT_TINY, text_color="#aaa6b8",
                               fg_color="#1e1e2e", corner_radius=999)
        tag_lbl.pack(side="right")

        desc_lbl = ctk.CTkLabel(tile, text=desc, font=FONT_SMALL, text_color=FG_SUBTLE,
                                wraplength=200, anchor="w", justify="left")
        desc_lbl.pack(anchor="w", padx=14, pady=(0, 12))

        def _click(_event=None, _t=tile, _aid=anc_id):
            self._select_anc(_t, _aid)

        for widget in (tile, top, name_lbl, desc_lbl, icon_label, icon_frame):
            widget.bind("<Button-1>", _click)

        return tile

    def _select_anc(self, selected_tile: ctk.CTkFrame, anc_id: int) -> None:
        for tile in self._anc_buttons:
            is_sel = tile is selected_tile
            tile.configure(
                fg_color=BG_ANC_SELECTED if is_sel else BG_ANC_TILE,
                border_color=BORDER_ANC_SEL if is_sel else BORDER_ANC,
            )
            tile._icon_frame.configure(fg_color=FG_ACCENT if is_sel else "#1e1535")
        self._anc_var.set(anc_id)
        if self.service.connected:
            self.service.set_anc_mode(anc_id)

    # --- EQ page -------------------------------------------------------

    def _build_eq_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color=BG_MAIN, corner_radius=0)
        page.grid_rowconfigure(2, weight=1)
        page.grid_columnconfigure(0, weight=1)

        # Header + controls
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 0))
        _label(hdr, "YOUR SOUND, YOUR RULES", font=FONT_EYEBROW, fg=FG_EYEBROW).pack(anchor="w")
        _label(hdr, "10-Band DSP Equalizer", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Fine-tune 16-bit parametric hardware filters stored on-chip",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        ctrl_row = ctk.CTkFrame(hdr, fg_color="transparent")
        ctrl_row.pack(anchor="w", pady=(8, 0))

        preset_names = list(self.presets.keys())
        self._preset_var = tk.StringVar(value=preset_names[0])
        self._preset_combo = ctk.CTkOptionMenu(
            ctrl_row, variable=self._preset_var,
            values=preset_names, width=220,
            fg_color=BG_CARD, button_color=FG_ACCENT, button_hover_color="#7a5ee0",
            dropdown_fg_color=BG_CARD,
            command=self._on_preset_changed,
        )
        self._preset_combo.pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl_row, text="Save as Preset", font=FONT_BOLD,
                      fg_color=FG_ACCENT, hover_color="#7a5ee0",
                      corner_radius=8, height=32, width=130,
                      command=self._save_preset).pack(side="left", padx=(0, 8))

        ctk.CTkButton(ctrl_row, text="Reset", font=FONT_MAIN,
                      fg_color=BG_CARD, hover_color="#1e1e30",
                      corner_radius=8, height=32,
                      command=lambda: self._apply_preset("Default Flat")).pack(side="left")

        self._preset_status = _label(hdr, "", font=FONT_TINY, fg=FG_TINY)
        self._preset_status.pack(anchor="w", pady=(4, 0))

        # EQ graph
        graph_wrap = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=14,
                                  border_width=1, border_color=BORDER_CARD)
        graph_wrap.grid(row=1, column=0, sticky="ew", padx=32, pady=(14, 0))

        self._eq_graph = EQGraph(graph_wrap, self.gains)
        self._eq_graph.pack(fill="both", expand=True, padx=2, pady=2)
        self._eq_graph.show_harman = True
        self._eq_graph.on_gain_change = self._on_graph_gain

        # Slider rack
        rack = ctk.CTkFrame(page, fg_color=BG_CARD, corner_radius=14,
                            border_width=1, border_color=BORDER_CARD)
        rack.grid(row=2, column=0, sticky="nsew", padx=32, pady=(10, 0))

        self._gain_value_labels: list[ctk.CTkLabel] = []
        self._sliders: list[ctk.CTkSlider] = []

        for i, freq in enumerate(EQ_FREQUENCIES):
            col = ctk.CTkFrame(rack, fg_color="transparent")
            col.pack(side="left", fill="both", expand=True, padx=3, pady=8)

            val_lbl = _label(col, "+0dB", font=FONT_TINY, fg=FG_TEXT, anchor="center")
            val_lbl.pack()
            self._gain_value_labels.append(val_lbl)

            slider = ctk.CTkSlider(
                col, from_=8, to=-8, variable=self._slider_vars[i],
                orientation="vertical", height=120,
                progress_color=FG_ACCENT, button_color=FG_TEXT,
                command=lambda v, idx=i: self._on_slider_changed(idx, v),
            )
            slider.pack(fill="y", expand=True)
            self._sliders.append(slider)

            freq_text = f"{freq // 1000}k" if freq >= 1000 else str(freq)
            _label(col, freq_text, font=FONT_TINY, fg=FG_SUBTLE, anchor="center").pack()

        # Harman checkbox
        harman_frame = ctk.CTkFrame(page, fg_color="transparent")
        harman_frame.grid(row=3, column=0, sticky="w", padx=32, pady=(8, 20))
        self._harman_check = ctk.CTkCheckBox(
            harman_frame, text="Show Harman Target 2019 reference curve",
            variable=self._show_harman_var, font=FONT_SMALL,
            checkmark_color=BG_MAIN, fg_color="#ffb552",
            command=self._on_harman_toggled,
        )
        self._harman_check.pack(anchor="w")

        # Apply default preset
        self._apply_preset("Harman Audiophile Target", send=False)
        return page

    # --- Buttons page --------------------------------------------------

    def _build_buttons_page(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._content_frame, fg_color=BG_MAIN, corner_radius=0)
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=32, pady=(26, 0))
        _label(hdr, "YOUR SOUND, YOUR RULES", font=FONT_EYEBROW, fg=FG_EYEBROW).pack(anchor="w")
        _label(hdr, "Button Remapping", font=FONT_HEADING).pack(anchor="w")
        _label(hdr, "Customize gestures and physical key actions stored in headphone memory",
               font=FONT_SMALL, fg=FG_SUBTLE).pack(anchor="w")

        card = ctk.CTkScrollableFrame(page, fg_color=BG_CARD, corner_radius=14,
                                      border_width=1, border_color=BORDER_CARD)
        card.grid(row=1, column=0, sticky="nsew", padx=32, pady=(18, 26))

        fn_names = [fname for _, fname in FUNCTIONS]
        fn_ids = [str(fid) for fid, _ in FUNCTIONS]

        for row in REMAP_ROWS:
            key = f"{row.key_number}_{row.action}"
            var = tk.StringVar(value=str(row.default_function))
            self._remap_vars[key] = var

            line = ctk.CTkFrame(card, fg_color="transparent", height=52)
            line.pack(fill="x", padx=6, pady=0)
            line.pack_propagate(False)

            # Badge
            badge = ctk.CTkLabel(line, text=row.badge, font=FONT_BADGE,
                                 text_color=FG_BADGE, fg_color="#1e1535",
                                 corner_radius=8, width=32, height=28)
            badge.pack(side="left", padx=(10, 0), pady=12)

            _label(line, row.key_name, font=FONT_BOLD).pack(side="left", padx=(8, 0))
            _label(line, row.gesture, font=FONT_SMALL, fg=FG_SUBTLE).pack(
                side="left", padx=(12, 0))

            combo = ctk.CTkOptionMenu(
                line, variable=var, values=fn_names, width=180,
                fg_color=BG_MAIN, button_color=FG_ACCENT, button_hover_color="#7a5ee0",
                dropdown_fg_color=BG_CARD,
                command=lambda _val, _r=row, _var=var, _ids=fn_ids, _names=fn_names:
                    self._on_remap_changed(_r, _var, _ids, _names),
            )
            combo.pack(side="right", padx=(0, 10))

            # Set displayed value to match default function name
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
                btn.configure(fg_color="#1c1535", text_color=FG_TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=FG_SUBTLE)
        if name == "eq":
            self.after(10, self._eq_graph.redraw)

    # ------------------------------------------------------------------
    # BLE callbacks (called on the tkinter main thread via after())
    # ------------------------------------------------------------------

    def _on_connection(self, connected: bool, status: str) -> None:
        if connected:
            self._status_dot.configure(text_color=FG_CONNECTED)
            self._status_text.configure(text="Connected")
            self._device_name_label.configure(text=status)
            self._device_subtitle.configure(text="Connected over Bluetooth LE")
            self._connect_btn.configure(
                text="Disconnect",
                text_color=FG_BATTERY_LOW,
                border_color="#3a2030",
                hover_color="#1e1020",
            )
        else:
            connecting = status.startswith("Connecting")
            failed = status.startswith("Error:")
            dot_state = "●  Connecting…" if connecting else ("Connection failed" if failed else "Disconnected")
            self._status_dot.configure(text_color=FG_SUBTLE if connecting else FG_DISCONNECTED)
            self._status_text.configure(text=dot_state)
            if connecting:
                self._device_subtitle.configure(text="Connecting…")
            elif failed:
                self._device_subtitle.configure(
                    text=status[len("Error: "):] if status.startswith("Error: ") else status
                )
            else:
                self._device_subtitle.configure(text="No device attached")
            self._connect_btn.configure(
                text="Connecting…" if connecting else ("Try Again" if failed else "Pair Headphones"),
                text_color=FG_ACCENT if not connecting else FG_SUBTLE,
                border_color=BORDER_CARD,
                hover_color="#1c1535",
            )
            self._battery_frame.pack_forget()
            self._firmware_label.pack_forget()

    def _on_battery(self, level: int) -> None:
        self._battery_label.configure(
            text=f"{level}%",
            text_color=FG_BATTERY_LOW if level <= 20 else FG_BATTERY,
        )
        self._battery_bar.set(level / 100)
        self._battery_bar.configure(
            progress_color=FG_BATTERY_LOW if level <= 20 else FG_CONNECTED
        )
        self._battery_frame.pack(fill="x", padx=14, pady=(4, 2))

    def _on_hardware_info(self, info: HardwareInfo) -> None:
        self._updating_hardware = True
        try:
            if info.device_name:
                self._device_name_label.configure(text=info.device_name)
            if info.firmware:
                self._firmware_label.configure(text=f"Firmware  {info.firmware}")
                self._firmware_label.pack(anchor="w", padx=14, pady=(2, 10))
            if info.gaming_mode is not None:
                self._gaming_var.set(info.gaming_mode)
            for key, function in info.key_settings.items():
                var = self._remap_vars.get(key)
                if var:
                    fn_val = 0 if function == 127 else function
                    var.set(FUNCTION_NAMES.get(fn_val, "Disabled"))
        finally:
            self._updating_hardware = False

    # ------------------------------------------------------------------
    # UI event handlers
    # ------------------------------------------------------------------

    def _on_connect_clicked(self) -> None:
        if self.service.connected:
            self.service.disconnect()
        else:
            self.service.connect()

    def _on_scan_clicked(self) -> None:
        """Start a BLE scan and show the device picker when results arrive."""
        if self.service.connected:
            self.service.disconnect()
        self._scan_btn.configure(text="⏳  Scanning…", state="disabled")
        self.service.on_scan_result = self._on_scan_result
        self.service.scan_all_devices()

    def _on_scan_result(self, devices: "list[ScannedDevice]") -> None:
        """Called on the UI thread when scan_all_devices() finishes."""
        self._scan_btn.configure(text="🔍  Scan & Pick Device", state="normal")
        self.service.on_scan_result = None
        if not devices:
            from tkinter import messagebox
            messagebox.showwarning(
                "No Devices Found",
                "No BLE devices were found.\n\n"
                "• Make sure the headphones are powered on.\n"
                "• Pair them once in Windows Settings → Bluetooth.",
                parent=self,
            )
            return
        DevicePickerDialog(self, devices, self._on_device_picked)

    def _on_device_picked(self, device: "ScannedDevice") -> None:
        """Called when the user selects a device in the picker dialog."""
        self.service.target_address = device.address
        label = device.name
        self._pinned_label.configure(text=f"📌 {label}")
        self._save_setting("target_address", device.address)
        self._save_setting("target_label", label)
        # Reconnect using the freshly pinned address
        self.service.connect()

    # ------------------------------------------------------------------
    # Persistent settings (device name, etc.)
    # ------------------------------------------------------------------

    @property
    def _settings_path(self) -> "Path":
        return (
            Path(os.environ.get("APPDATA", "~")).expanduser()
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
        if not self._updating_hardware and self.service.connected:
            self.service.set_gaming_mode(self._gaming_var.get())

    def _on_slider_changed(self, index: int, value: float) -> None:
        gain = round(value * 2) / 2
        self.gains[index] = gain
        sign = "+" if gain >= 0 else ""
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
        self._eq_after_id = self.after(300, self._send_eq)

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
            self.gains[i] = float(gain)
            self._slider_vars[i].set(gain)
            sign = "+" if gain >= 0 else ""
            self._gain_value_labels[i].configure(text=f"{sign}{gain:g}dB")
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
            "Give this sound a name:",
            initialvalue=f"Custom {number}",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = self._unique_custom_name(name.strip())
        self.custom_presets[name] = self.gains.copy()
        self.presets[name] = self.gains.copy()
        # Update the option menu
        self._preset_combo.configure(values=list(self.presets.keys()))
        self._preset_var.set(name)
        try:
            self._persist_custom_presets()
            self._preset_status.configure(text=f'Saved "{name}" on this computer')
        except OSError as err:
            self._preset_status.configure(text=f"Could not save preset: {err}")

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
    # Window lifecycle
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._eq_after_id is not None:
            self.after_cancel(self._eq_after_id)
        self.service.close()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    app = MainWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
