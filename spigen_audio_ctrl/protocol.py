"""Encoding and decoding for the headphones' proprietary RCSP protocol.

This file is platform-independent — identical to the Linux version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from struct import pack


EQ_FREQUENCIES = (60, 220, 500, 1000, 2000, 2500, 5000, 7500, 12000, 16000)
EQ_Q_VALUES = (0.4, 1.2, 1.5, 1.5, 1.2, 0.8, 1.2, 1.2, 1.2, 1.2)


@dataclass
class HardwareInfo:
    device_name: str | None = None
    firmware: str | None = None
    gaming_mode: bool | None = None
    key_settings: dict[str, int] = field(default_factory=dict)


def pack_rcsp(opcode: int, payload: bytes, sequence: int = 1, has_response: bool = True) -> bytes:
    body = bytes((sequence & 0xFF,)) + payload
    flag = 0xC0 if has_response else 0x80
    return b"\xfe\xdc\xba" + bytes((flag, opcode & 0xFF)) + len(body).to_bytes(2, "big") + body + b"\xef"


def encode_anc(anc_id: int, sequence: int = 1) -> bytes:
    b1, b2, b3 = (anc_id >> 16) & 0xFF, (anc_id >> 8) & 0xFF, anc_id & 0xFF
    # Quirk used by the vendor implementation for the transparency profile.
    if (b1, b2, b3) == (3, 1, 0):
        b2 = 2
    return pack_rcsp(0xFF, bytes((23, 3, b1, b2, b3)), sequence)


def encode_gaming_mode(enabled: bool, sequence: int = 1) -> bytes:
    return pack_rcsp(0xC0, bytes((2, 5, 2 if enabled else 1)), sequence)


def encode_key_mapping(key_number: int, action: int, function: int, sequence: int = 1) -> bytes:
    jl_function = 127 if function == 0 else function
    return pack_rcsp(0xC0, bytes((2, key_number, action, jl_function)), sequence)


def encode_equalizer(gains: list[float], sequence: int = 1) -> bytes:
    """Pack a 10-band EQ into an RCSP write packet."""
    payload = bytearray()
    for gain, q in zip(gains, EQ_Q_VALUES):
        raw = int(round(gain * 10))
        payload += pack(">h", max(-80, min(80, raw)))
        payload += pack(">H", int(round(q * 100)))
    return pack_rcsp(0xC0, bytes([2, 3]) + bytes(payload), sequence)


def parse_target_battery(data: bytes) -> int | None:
    """Extract battery level from an RCSP response, if present."""
    if len(data) > 8 and data[:3] == b"\xfe\xdc\xba" and data[4] == 0x0A:
        return data[8] if 0 <= data[8] <= 100 else None
    return None


def parse_hardware_info(data: bytes) -> HardwareInfo:
    """Decode an RCSP 0xC1 hardware-info response."""
    info = HardwareInfo()
    if len(data) < 10:
        return info
    try:
        payload = data[7:-1]
        if len(payload) < 2:
            return info
        sub = payload[1]

        if sub == 5:
            # Gaming mode state
            if len(payload) >= 4:
                info.gaming_mode = payload[3] == 2
        elif sub == 3:
            # EQ / key map info block — parse key settings
            offset = 2
            key_settings: dict[str, int] = {}
            while offset + 2 < len(payload):
                key_num = payload[offset]
                action = payload[offset + 1]
                func = payload[offset + 2]
                key_settings[f"{key_num}_{action}"] = func
                offset += 3
            if key_settings:
                info.key_settings = key_settings
        elif sub == 0xFF:
            # Device name / firmware info block
            offset = 2
            while offset + 1 < len(payload):
                tag = payload[offset]
                length = payload[offset + 1]
                value = payload[offset + 2: offset + 2 + length]
                if tag == 1:
                    info.device_name = value.decode("utf-8", errors="replace").strip("\x00")
                elif tag == 2:
                    info.firmware = value.decode("utf-8", errors="replace").strip("\x00")
                offset += 2 + length
    except Exception:
        pass
    return info
