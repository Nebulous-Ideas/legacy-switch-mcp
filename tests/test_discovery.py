from pathlib import Path

import pytest

from switch_mcp import discovery

FIX = Path(__file__).parent / "fixtures"


def test_request_matches_real_capture():
    """The bytes SmartServer.exe really sent, rebuilt from its parts."""
    real = (FIX / "dlink_request_broadcast.bin").read_bytes()
    mac = bytes.fromhex("00005e005301")
    assert discovery.build_request(mac, 0x01945AE0, 1) == real


def test_request_is_forty_bytes_and_carries_oem_id():
    req = discovery.build_request(None, 0x01945AD8, 2)
    assert len(req) == 40
    assert req[16:20].hex() == "01945ad8"
    assert req[20:26] == bytes(6)


def test_parse_real_dlink_reply():
    info = discovery.parse_reply((FIX / "dlink_reply.bin").read_bytes())
    assert info["model"] == "DGS-1100-08"
    assert info["name"] == "demo-dlk"
    assert info["ip"] == "198.51.100.3"
    assert info["netmask"] == "255.255.255.0"
    assert info["gateway"] == "198.51.100.1"
    assert info["firmware"] == "1.10.039"
    assert info["mac"] == "00:00:5e:00:53:02"


def test_parse_rejects_junk():
    with pytest.raises(ValueError):
        discovery.parse_reply(b"hello")


def test_parse_stops_on_truncated_tlv():
    good = (FIX / "dlink_reply.bin").read_bytes()
    info = discovery.parse_reply(good[:-10])  # cuts the firmware TLV; earlier fields must survive
    assert info["model"] == "DGS-1100-08"
    assert "firmware" not in info


def test_bad_mac_length_rejected():
    with pytest.raises(ValueError):
        discovery.build_request(b"\x00\x01", 1, 1)
