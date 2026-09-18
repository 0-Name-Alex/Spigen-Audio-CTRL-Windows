# Spigen Audio CTRL — Windows

A native Windows desktop controller for the **Spigen SA-HP P10** headphones.
Ported from the Linux/BlueZ version to run on Windows using the WinRT Bluetooth LE stack.

## Features

- Six ANC profiles: Deep, Adaptive, Commuting, Anti-Wind, Transparency, and Off
- Low-latency Gaming Mode toggle with live state sync
- 10-band hardware DSP EQ with draggable graph, Harman target overlay, presets and custom preset slots
- Button remapping for MFB single/double/triple click and volume-button single/long press
- Battery level display
- Automatic reconnect on signal loss

All settings are sent directly to the headphone hardware using the same RCSP packets as the
official Windows application. EQ and button assignments are stored on the headphones, not applied
as a Windows audio effect.

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10 1903+ | WinRT Bluetooth LE API is required |
| Python 3.11+ | Download from [python.org](https://python.org) — tick "Add to PATH" |
| Bluetooth adapter | Any BT 4.0+ adapter; built-in or USB dongle |

## Setup

**1. Install Python dependencies**

Open a terminal (Command Prompt or PowerShell) in this folder and run:

```cmd
pip install -r requirements.txt
```

**2. Pair the headphones**

Open **Settings → Bluetooth & devices** and pair your Spigen SA-HP P10 headphones *once*.
After that the app will find them automatically each time.

**3. Run the app**

Double-click **`run.bat`**, or in the terminal:

```cmd
python -m spigen_audio_ctrl
```

## Build a standalone .exe (optional)

If you want a single executable that doesn't require Python installed:

```cmd
build_exe.bat
```

The `.exe` will appear in the `dist\` folder.

## Troubleshooting

| Problem | Solution |
|---|---|
| "P10 not found" | Make sure headphones are powered on and previously paired in Windows Settings |
| "RCSP write characteristic not found" | Turn the headphones off and on, then reconnect |
| App crashes on startup | Ensure `pip install -r requirements.txt` completed without errors |
| Bluetooth not working | Windows 10 1903 or later is required for the WinRT BLE API |

## Architecture

| File | Purpose |
|---|---|
| `spigen_audio_ctrl/protocol.py` | Platform-independent RCSP packet encoder/decoder (unchanged from Linux) |
| `spigen_audio_ctrl/bluetooth_win.py` | Windows BLE transport using `bleak` + asyncio |
| `spigen_audio_ctrl/app_win.py` | Desktop UI using `customtkinter` + tkinter Canvas EQ graph |

## Credits

Original Linux implementation by [SurajPa05](https://github.com/SurajPa05/Spigen-Audio-CTRL-Linux).
Windows port preserves all features and the RCSP protocol is bit-for-bit compatible.
