"""UDP discovery for Cameo-built smart switches (D-Link DGS-1100 EasySmart, USRobotics 7724).

Protocol decoded from a capture of D-Link's SmartServer.exe (2026-11-08):
  request  40 bytes to 255.255.255.255:64515 (from :64513, flag 1) and 239.255.255.100:64515 (from :64519, flag 2)
  reply    broadcast from :64515 to 255.255.255.255:64513, 40-byte header then TLVs (type u16, length u16 incl. header)
The 4 bytes after `00010000` are a per-OEM ID; the switch ignores requests that carry another vendor's value.
Only requests are sent: nothing here changes a switch.
"""
from __future__ import annotations

import fcntl
import socket
import struct
import time

GROUP = "239.255.255.100"
REQUEST_PORT = 64515
REPLY_PORT = 64513
MULTICAST_SRC_PORT = 64519
HEADER_LEN = 40

OEM_IDS = {0x01945AE0: "dlink", 0x01945AD8: "usrobotics"}
TLV_NAMES = {1: "model", 2: "name", 3: "location", 4: "ip", 5: "netmask", 6: "gateway", 7: "trap_ip", 0x0B: "firmware"}
_IP_TLVS = {4, 5, 6, 7}


def build_request(mac: bytes | None, oem_id: int, flag: int) -> bytes:
    """40-byte discovery request. flag 1 = broadcast variant, 2 = multicast variant. mac may be None (older utilities send zeros)."""
    mac = mac if mac is not None else bytes(6)
    if len(mac) != 6:
        raise ValueError("mac must be 6 bytes")
    return (
        struct.pack(">HH", 2, 0) + bytes(8) + bytes.fromhex("00010000")
        + struct.pack(">I", oem_id) + mac + struct.pack(">H", flag) + bytes(12)
    )


def parse_reply(buf: bytes) -> dict:
    """Decode a switch reply. Raises ValueError if it is not one."""
    if len(buf) < HEADER_LEN + 4 or buf[:2] != b"\x00\x02":
        raise ValueError("not a discovery reply")
    out: dict = {"mac": buf[4:10].hex(":")}
    i = HEADER_LEN
    while i + 4 <= len(buf):
        t, length = struct.unpack(">HH", buf[i:i + 4])
        if length < 4 or i + length > len(buf):
            break
        value = buf[i + 4:i + length]
        name = TLV_NAMES.get(t)
        if name in ("ip", "netmask", "gateway", "trap_ip") and len(value) == 4:
            out[name] = socket.inet_ntoa(value)
        elif name:
            out[name] = value.split(b"\0")[0].decode("latin1").strip()
        i += length
    return out


def _ioctl(ifname: str, request: int) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        return fcntl.ioctl(s.fileno(), request, struct.pack("256s", ifname[:15].encode()))


def interface_ip(ifname: str) -> str:
    return socket.inet_ntoa(_ioctl(ifname, 0x8915)[20:24])


def interface_mac(ifname: str) -> bytes:
    return _ioctl(ifname, 0x8927)[18:24]


def list_interfaces() -> list[dict]:
    """IPv4 interfaces that could carry discovery (loopback skipped)."""
    result = []
    for _, name in socket.if_nameindex():
        if name == "lo":
            continue
        try:
            result.append({"interface": name, "ip": interface_ip(name)})
        except OSError:
            continue
    return result


def _collect(rx: socket.socket, wait: float) -> dict[str, dict]:
    found: dict[str, dict] = {}
    end = time.time() + wait
    while time.time() < end:
        try:
            buf, addr = rx.recvfrom(2048)
        except socket.timeout:
            continue
        try:
            found[addr[0]] = parse_reply(buf)
        except ValueError:
            continue
    return found


def _receiver(ifname: str | None) -> socket.socket:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if ifname:
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, ifname.encode())
    rx.bind(("", REPLY_PORT))
    rx.settimeout(0.4)
    return rx


def discover(ifname: str, wait: float = 3.0) -> list[dict]:
    """Broadcast+multicast discovery on one interface, once per known OEM ID. Returns one dict per switch heard."""
    ip, mac = interface_ip(ifname), interface_mac(ifname)
    switches: dict[str, dict] = {}
    for oem_id, oem in OEM_IDS.items():
        rx = _receiver(ifname)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tx.bind((ip, MULTICAST_SRC_PORT))
            tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            tx.sendto(build_request(mac, oem_id, 2), (GROUP, REQUEST_PORT))
            rx.sendto(build_request(mac, oem_id, 1), ("255.255.255.255", REQUEST_PORT))
            for addr, info in _collect(rx, wait).items():
                info.update({"address": addr, "interface": ifname, "oem": oem})
                switches[addr] = info
        finally:
            rx.close()
            tx.close()
    return list(switches.values())


def probe(host: str, wait: float = 3.0) -> dict | None:
    """Ask one switch directly (works across subnets). Returns its reply or None."""
    socket.inet_aton(host)  # raises on a bad address
    for oem_id, oem in OEM_IDS.items():
        rx = _receiver(None)
        try:
            rx.sendto(build_request(None, oem_id, 1), (host, REQUEST_PORT))
            for addr, info in _collect(rx, wait).items():
                if addr == host:
                    info.update({"address": addr, "oem": oem})
                    return info
        finally:
            rx.close()
    return None
