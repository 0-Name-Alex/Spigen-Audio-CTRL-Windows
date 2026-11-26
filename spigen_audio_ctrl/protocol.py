"""Encoding and decoding for the headphones' proprietary RCSP protocol."""

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
    return pack_rcsp(0xC0, bytes((4, 2, key_number & 0xFF, action & 0xFF, jl_function & 0xFF)), sequence)


def encode_equalizer(
    gains: list[float] | tuple[float, ...],
    mode: int = 126,
    master_gain: float = 0.0,
    sequence: int = 1,
) -> bytes:
    if len(gains) != 10:
        raise ValueError("the headphone DSP requires exactly 10 EQ gains")
    payload = bytearray((0x20, 63, mode & 0xFF))
    payload.extend(pack("<h", round(master_gain * 100)))
    for frequency, gain, q_value in zip(EQ_FREQUENCIES, gains, EQ_Q_VALUES):
        if not -8.0 <= gain <= 8.0:
            raise ValueError("EQ gains must be between -8 and +8 dB")
        payload.extend(pack("<Hhh", frequency, round(gain * 100), round(q_value * 100)))
    return pack_rcsp(0xFF, bytes(payload), sequence)


def parse_hardware_info(data: bytes) -> HardwareInfo:
    info = HardwareInfo()
    if len(data) < 14:
        return info
    pointer = 13
    while pointer < len(data) - 2:
        tag_length = data[pointer]
        if tag_length <= 0 or pointer + 1 >= len(data):
            break
        tag = data[pointer + 1]
        value_length = tag_length - 1
        value_start = pointer + 2
        if value_start + value_length > len(data):
            break
        value = data[value_start:value_start + value_length]
        pointer += tag_length + 1
        if tag == 1:
            info.device_name = value.rstrip(b"\0").decode("utf-8", "replace")
        elif tag == 2:
            for index in range(0, len(value) - 2, 3):
                info.key_settings[f"{value[index]}_{value[index + 1]}"] = value[index + 2]
        elif tag == 5 and value:
            info.gaming_mode = value[0] == 2
        elif tag == 9:
            info.firmware = value.rstrip(b"\0").decode("utf-8", "replace")
    return info


def parse_target_battery(data: bytes) -> int | None:
    """Read battery TLV from an opcode 0x03 response, if present."""
    if len(data) < 10 or data[:3] != b"\xfe\xdc\xba" or data[4] != 0x03:
        return None
    pointer = 8
    while pointer + 2 < len(data) - 1:
        tag_length = data[pointer]
        if tag_length <= 0 or pointer + tag_length >= len(data):
            break
        tag, value_start = data[pointer + 1], pointer + 2
        if tag == 1 and tag_length >= 2:
            level = data[value_start]
            return level if 0 <= level <= 100 else None
        pointer += tag_length + 1
    return None
