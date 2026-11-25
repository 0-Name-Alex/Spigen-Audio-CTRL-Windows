# Spigen Audio CTRL (Windows) — AI Agent Guide

Windows desktop controller for **Spigen SA-HP P10** headphones over Bluetooth Low Energy (**BLE GATT**), built with **Python + `bleak` + Tkinter + `pystray`**.

## 1. Repository & Folder Note (CRITICAL)
- `D:\Projects\_spigen_clean` is the **Git repository** synced to `0-Name-Alex/Spigen-Audio-CTRL-Windows`.
- `D:\Projects\Spigen-Audio-CTRL-Windows` is a local working/build directory containing PyInstaller `build/` / `dist/` outputs without `.git`.
- Always commit and push code changes from `_spigen_clean` (or keep both in sync).

## 2. Architecture & Protocol Spec
- [`spigen_audio_ctrl/protocol.py`](spigen_audio_ctrl/protocol.py): Platform-independent binary RCSP packet codec:
  - Frame layout: `0xFE 0xDC 0xBA` | `flag` (`0xC0` response expected, `0x80` no response) | `opcode` | `2-byte big-endian payload length` | `[sequence] + payload` | `0xEF`.
  - Encoders: `encode_anc` (opcode `0xFF`, contains vendor quirk mapping `(3, 1, 0)` -> `(3, 2, 0)` for transparency), `encode_gaming_mode` (`0xC0`), `encode_key_mapping` (`0xC0`), `encode_equalizer` (10 bands at `EQ_FREQUENCIES`, Q-values `EQ_Q_VALUES`, clamping gains `[-8.0, +8.0]` dB).
  - Decoders: `parse_target_battery` (`0x0A`), `parse_hardware_info` (`0xC1`).
- [`spigen_audio_ctrl/bluetooth_win.py`](spigen_audio_ctrl/bluetooth_win.py): Async Windows WinRT BLE client using `bleak`:
  - GATT characteristics: write `"ae01"`, notify `"ae02"`, standard battery `"2a19"`.
  - `_uuid_matches` strictly enforces `0000{short}` prefix matching for 4-char SIG UUIDs vs substring matching for longer vendor UUIDs—preserve this to prevent false matches.
  - Connects/reconnects by saved BLE **address** after initial device-picker scan (`scan_all_devices()`) so arbitrary/emoji device names work.
- [`spigen_audio_ctrl/app_win.py`](spigen_audio_ctrl/app_win.py) & [`run.py`](run.py): Tkinter GUI + system tray lifecycle bridging asyncio BLE loop on a background thread.

## 3. Build & Run Commands
- **Install dependencies**: `py -3.14 -m pip install -r requirements.txt`
- **Run from source**: `py -3.14 run.py` (or `.\run.bat`)
- **Build standalone EXE**: `.\build_exe.bat` (outputs `dist\SpigenAudioCTRL.exe` via PyInstaller `--onefile --windowed`).
- **Syntax / protocol check**: `py -3.14 -m py_compile spigen_audio_ctrl/*.py run.py`
