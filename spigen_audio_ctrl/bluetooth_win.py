"""Windows BLE transport for the Spigen headphone GATT protocol.

Replaces the Linux BlueZ/D-Bus bluetooth.py with a cross-platform
implementation built on *bleak* (Windows Bluetooth WinRT backend).

Key improvements:
  • scan_all_devices() returns every visible BLE device so the user can
    pick theirs from a list — works with ANY name, including emoji.
  • Once a device is chosen its *address* is stored and used for all
    future connections, so full scanning is only needed once.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable, List

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from . import protocol

# GATT characteristic UUIDs (short form — matched as substring of full UUID)
_WRITE_SHORT    = "ae01"
_NOTIFY_SHORT   = "ae02"
_BATTERY_SHORT  = "2a19"

# Timing (seconds)
_SCAN_TIMEOUT       = 10.0   # device-picker scan duration
_CONNECT_SCAN       = 12.0   # targeted scan when reconnecting by address
_POST_CONNECT_DELAY = 0.15   # settle time before querying hardware
_RECONNECT_DELAY    = 5.0    # pause before auto-reconnect


# ---------------------------------------------------------------------------
# Helpers (module level so they don't accidentally break class indentation)
# ---------------------------------------------------------------------------

def _uuid_matches(uuid: str, short: str) -> bool:
    """Match a GATT characteristic UUID against a short pattern.

    For 4-character short UUIDs (e.g. '0001', '0002', '2a19'):
        Only match at the Bluetooth SIG UUID prefix position — i.e.
        the UUID must start with 0000{short} (case-insensitive).
        This prevents '0001' from falsely matching 00000002-0000-**1**0**0**0-...

    For longer vendor-specific patterns (e.g. 'ae01', 'ae02'):
        Match as substring anywhere in the cleaned UUID.
    """
    cleaned = uuid.lower().replace("-", "")
    short = short.lower()
    if len(short) <= 4:
        # Precise SIG UUID match: 0000XXXX-0000-1000-...
        return cleaned[:8] == f"0000{short}"
    # Vendor-specific: substring match anywhere
    return short in cleaned


def _keyword_match(name: str | None) -> bool:
    """Match factory Spigen names when no device has been pinned."""
    if not name:
        return False
    u = name.upper()
    return "SPIGEN" in u or "SA-HP" in u or "P10" in u


def _get_windows_paired_names() -> dict:
    """
    Return a dict mapping UPPERCASE Bluetooth address → friendly name
    for all devices paired in Windows.

    Reads from the Windows registry key:
      HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters\\Devices
    Each sub-key is the address (12 hex chars), and the value 'FriendlyName'
    is a byte string with the user-assigned name.

    Returns an empty dict on any error (non-Windows, permission denied, etc.).
    """
    names: dict = {}
    try:
        import winreg
        key_path = (
            r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
        )
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as devices_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(devices_key, i)
                    i += 1
                    # subkey_name is 12 hex chars — convert to XX:XX:XX:XX:XX:XX
                    raw_addr = subkey_name.upper()
                    if len(raw_addr) == 12:
                        mac = ":".join(
                            raw_addr[j:j+2] for j in range(0, 12, 2)
                        )
                    else:
                        mac = raw_addr
                    with winreg.OpenKey(devices_key, subkey_name) as dev_key:
                        try:
                            value, _ = winreg.QueryValueEx(dev_key, "FriendlyName")
                            if isinstance(value, bytes):
                                friendly = value.rstrip(b"\x00").decode(
                                    "utf-16-le", errors="replace"
                                ).strip()
                            else:
                                friendly = str(value).strip()
                            if friendly:
                                names[mac] = friendly
                        except FileNotFoundError:
                            pass
                except OSError:
                    break
    except Exception:
        pass
    return names


@dataclass
class ScannedDevice:
    """A BLE device returned by the device-picker scan."""
    name: str
    address: str
    rssi: int


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

class BluetoothService:
    """Serializes GATT operations; fires callbacks on the tkinter main thread."""

    def __init__(self, schedule_callback: Callable[[Callable], None]) -> None:
        """
        Parameters
        ----------
        schedule_callback:
            Schedules a zero-argument callable on the tkinter main thread.
            Use ``lambda fn: root.after(0, fn)``.
        """
        self._schedule = schedule_callback

        # ── State ─────────────────────────────────────────────────────
        self.device_name: str = "Spigen SA-HP P10"
        # Pinned address — set when user picks from the device list.
        # Empty string means fall back to keyword name matching.
        self.target_address: str = ""

        self.battery_level: int | None = None
        self.connected: bool = False
        self._connecting: bool = False
        self._manual_disconnect: bool = False
        self._sequence: int = 1
        self._write_uuid: str | None = None
        self._write_with_response: bool = False   # True only if char has 'write' (not just write-without-response)
        self._client: BleakClient | None = None

        # ── Background asyncio loop ────────────────────────────────────
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        threading.Thread(
            target=self._run_loop, daemon=True, name="spigen-ble"
        ).start()

        # ── Public callbacks ───────────────────────────────────────────
        self.on_connection: Callable[[bool, str], None] | None = None
        self.on_battery: Callable[[int], None] | None = None
        self.on_hardware_info: Callable[[protocol.HardwareInfo], None] | None = None
        self.on_log: Callable[[str, str], None] | None = None
        # Called with List[ScannedDevice] when scan_all_devices() finishes
        self.on_scan_result: Callable[[List[ScannedDevice]], None] | None = None

        self._reconnect_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _emit(self, callback, *args) -> None:
        if callback:
            fn, a = callback, args
            self._schedule(lambda: fn(*a))

    def _log(self, category: str, message: str) -> None:
        print(f"[{category}] {message}")
        self._emit(self.on_log, category, message)

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % 255 or 1
        return self._sequence

    # ------------------------------------------------------------------
    # Device-picker scan
    # ------------------------------------------------------------------

    def scan_all_devices(self) -> None:
        """Scan all visible BLE devices; calls on_scan_result on the UI thread."""
        self._submit(self._scan_all_worker())

    async def _scan_all_worker(self) -> None:
        self._log("BLE", f"Scanning all BLE devices for {int(_SCAN_TIMEOUT)}s…")
        try:
            raw = await BleakScanner.discover(timeout=_SCAN_TIMEOUT, return_adv=True)
            # raw: dict[address, (BLEDevice, AdvertisementData)]

            # Try to read Windows paired-device names from the registry so the
            # picker can show both the BLE advertisement name AND the name the
            # user set in Windows Settings → Bluetooth.
            win_names = _get_windows_paired_names()

            results: List[ScannedDevice] = []
            for address, (dev, adv) in raw.items():
                ble_name = dev.name or adv.local_name or "(unknown)"
                # Check if Windows knows this address under a different (custom) name
                win_name = win_names.get(address.upper(), "")
                if win_name and win_name != ble_name:
                    display_name = f"{ble_name}  [{win_name}]"
                else:
                    display_name = ble_name
                rssi = adv.rssi if adv.rssi is not None else -999
                results.append(ScannedDevice(
                    name=display_name, address=address, rssi=rssi
                ))
            results.sort(key=lambda d: d.rssi, reverse=True)
            self._log("BLE", f"Found {len(results)} BLE device(s)")
            self._emit(self.on_scan_result, results)
        except Exception as exc:
            self._log("ERR", f"Scan failed: {exc}")
            self._emit(self.on_scan_result, [])

    # ------------------------------------------------------------------
    # Connect flow
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initiate a BLE connection (non-blocking, called from UI thread)."""
        if self.connected or self._connecting:
            return
        self._manual_disconnect = False
        self._connecting = True
        self._emit(self.on_connection, False, "Connecting…")
        self._submit(self._connect_worker())

    async def _find_device(self) -> BLEDevice | None:
        """Return a BLEDevice — by pinned address if known, else keyword scan."""
        if self.target_address:
            self._log("BLE", f"Connecting to pinned device {self.target_address}…")
            dev = await BleakScanner.find_device_by_address(
                self.target_address, timeout=_CONNECT_SCAN
            )
            if dev is None:
                self._log("BLE", "Pinned device not seen — scanning broadly…")
                # Maybe the address rotated or it's advertising under a different handle;
                # fall back to a broad BLE scan and match by address substring.
                raw = await BleakScanner.discover(timeout=_CONNECT_SCAN, return_adv=True)
                target = self.target_address.upper()
                for addr, (d, _adv) in raw.items():
                    if addr.upper() == target:
                        return d
            return dev
        self._log("BLE", "Scanning for Spigen SA-HP P10 (keyword)…")
        return await BleakScanner.find_device_by_filter(
            lambda d, _adv: _keyword_match(d.name),
            timeout=_CONNECT_SCAN,
        )

    async def _connect_worker(self) -> None:
        try:
            device = await self._find_device()
            if device is None:
                raise RuntimeError(
                    "Headphones not found.\n"
                    "• Make sure they are powered on and nearby.\n"
                    "• Pair them in Windows Settings → Bluetooth.\n"
                    "• Or click  Scan & Pick  to select your device."
                )

            self.device_name = device.name or self.target_address or "SA-HP P10"
            self._log("BLE", f"Found '{self.device_name}' — connecting…")

            client = BleakClient(
                device,
                disconnected_callback=self._on_disconnected,
            )
            await client.connect(timeout=15.0)
            self._client = client

            # ── GATT service discovery ────────────────────────────────
            # client.services is populated immediately after connect() in
            # bleak 0.22+. We retry briefly for slow dual-mode devices.
            services = client.services
            if not list(services):
                for attempt in range(4):
                    await asyncio.sleep(1.0)
                    services = client.services
                    if list(services):
                        break
                    self._log("BLE", f"Waiting for GATT services… (attempt {attempt + 1}/4)")

            # Log everything we found — helps diagnose wrong-device selection
            all_uuids = []
            for svc in services:
                for char in svc.characteristics:
                    all_uuids.append(f"{char.uuid}  props={char.properties}")
            if all_uuids:
                self._log("BLE", f"GATT characteristics found ({len(all_uuids)}):")
                for u in all_uuids:
                    self._log("GATT", u)
            else:
                self._log("BLE", "⚠ No GATT characteristics found — wrong device or not BLE-capable")

            # Match characteristics — try both ae-prefix UUIDs and 0001/0002 fallbacks
            _WRITE_PATTERNS  = [_WRITE_SHORT,  "0001"]
            _NOTIFY_PATTERNS = [_NOTIFY_SHORT, "0002"]

            write_uuid = None
            notify_uuids: list[str] = []
            battery_uuid: str | None = None

            for svc in services:
                for char in svc.characteristics:
                    uuid = char.uuid.lower()
                    props = char.properties
                    is_writable = "write" in props or "write-without-response" in props
                    if write_uuid is None and is_writable and any(
                        _uuid_matches(uuid, p) for p in _WRITE_PATTERNS
                    ):
                        write_uuid = char.uuid
                        # Use response=True only if char explicitly supports Write Request
                        write_with_response = "write" in props
                    if any(_uuid_matches(uuid, p) for p in _NOTIFY_PATTERNS) \
                            and "notify" in props:
                        notify_uuids.append(char.uuid)
                    if _uuid_matches(uuid, _BATTERY_SHORT):
                        battery_uuid = char.uuid

            if write_uuid is None:
                await client.disconnect()
                raise RuntimeError(
                    "RCSP control service not found on this device.\n\n"
                    + (
                        "No GATT characteristics were found at all.\n"
                        "This is almost certainly the CLASSIC Bluetooth address.\n\n"
                        if not all_uuids else
                        f"{len(all_uuids)} characteristic(s) found but none matched RCSP.\n"
                        "Check the console output for the UUIDs — the correct device\n"
                        "should expose a characteristic containing 'ae01' in its UUID.\n\n"
                    )
                    + "→ Click  Scan & Pick  and choose a DIFFERENT entry.\n"
                    "  Your custom name only shows on the Classic entry.\n"
                    "  The BLE/control entry usually shows the model number (e.g. SP...)."
                )

            self._write_uuid = write_uuid
            self._write_with_response = write_with_response
            self._log("BLE", f"Write char: {write_uuid}  response={write_with_response}")

            for nuuid in notify_uuids:
                try:
                    await client.start_notify(nuuid, self._on_notify)
                except Exception as exc:
                    self._log("BLE", f"Notify subscribe failed {nuuid}: {exc}")

            if battery_uuid:
                try:
                    data = await client.read_gatt_char(battery_uuid)
                    if data:
                        self._set_battery(data[0])
                except Exception:
                    pass

            self.connected = True
            self._connecting = False
            self._emit(self.on_connection, True, self.device_name)
            self._log("BLE", "Hardware engine attached and ready")
            await asyncio.sleep(_POST_CONNECT_DELAY)
            await self._query_hardware_async()

        except Exception as error:
            self.connected = False
            self._connecting = False
            self._log("ERR", str(error))
            self._emit(self.on_connection, False, f"Error: {error}")

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def _on_disconnected(self, _client: BleakClient) -> None:
        was_connected = self.connected
        self.connected = False
        self._write_uuid = None
        self._write_with_response = False
        self._client = None
        if was_connected:
            self._emit(self.on_connection, False, "Disconnected")
            if not self._manual_disconnect:
                self._log("BLE", f"Disconnected — reconnecting in {_RECONNECT_DELAY}s…")
                self._reconnect_handle = self._loop.call_later(
                    _RECONNECT_DELAY, self._schedule_reconnect
                )

    def _schedule_reconnect(self) -> None:
        self._reconnect_handle = None
        if not self._manual_disconnect and not self.connected:
            self._submit(self._connect_worker())

    def disconnect(self) -> None:
        self._manual_disconnect = True
        self._connecting = False
        if self._reconnect_handle:
            self._reconnect_handle.cancel()
            self._reconnect_handle = None
        self._submit(self._disconnect_worker())

    async def _disconnect_worker(self) -> None:
        client = self._client
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass
        self.connected = False
        self._connecting = False
        self._write_uuid = None
        self._client = None
        self.battery_level = None
        self._emit(self.on_connection, False, "Disconnected")

    # ------------------------------------------------------------------
    # Notifications / Battery
    # ------------------------------------------------------------------

    def _on_notify(self, _handle: int, data: bytearray) -> None:
        raw = bytes(data)
        self._log("RX", raw.hex().upper())
        battery = protocol.parse_target_battery(raw)
        if battery is not None:
            self._set_battery(battery)
        if len(raw) > 4 and raw[:3] == b"\xfe\xdc\xba" and raw[4] == 0xC1:
            self._emit(self.on_hardware_info, protocol.parse_hardware_info(raw))

    def _set_battery(self, level: int) -> None:
        if 0 <= level <= 100:
            self.battery_level = level
            self._emit(self.on_battery, level)

    # ------------------------------------------------------------------
    # Write / Commands
    # ------------------------------------------------------------------

    def send(self, packet: bytes) -> None:
        self._submit(self._write_packet(packet))

    async def _write_packet(self, packet: bytes) -> bool:
        client, uuid = self._client, self._write_uuid
        if not self.connected or client is None or uuid is None:
            self._log("ERR", "Headphones are not connected")
            return False
        try:
            await client.write_gatt_char(uuid, packet, response=self._write_with_response)
            self._log("TX", packet.hex().upper())
            return True
        except Exception as error:
            self._log("ERR", f"Write failed: {error}")
            return False

    def set_anc_mode(self, anc_id: int) -> None:
        self.send(protocol.encode_anc(anc_id, self._next_sequence()))

    def set_equalizer(self, gains: list) -> None:
        self.send(protocol.encode_equalizer(gains, sequence=self._next_sequence()))

    def set_gaming_mode(self, enabled: bool) -> None:
        self.send(protocol.encode_gaming_mode(enabled, self._next_sequence()))

    def set_key_mapping(self, key_number: int, action: int, function: int) -> None:
        self.send(protocol.encode_key_mapping(
            key_number, action, function, self._next_sequence()
        ))

    def query_hardware(self) -> None:
        self._submit(self._query_hardware_async())

    async def _query_hardware_async(self) -> None:
        await self._write_packet(
            protocol.pack_rcsp(0x03, b"\xff\xff\xff\xff\x00", self._next_sequence())
        )
        await asyncio.sleep(0.08)
        await self._write_packet(
            protocol.pack_rcsp(0xC1, b"\xff\xff\xff\xff", self._next_sequence())
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.disconnect()
        self._loop.call_soon_threadsafe(self._loop.stop)
