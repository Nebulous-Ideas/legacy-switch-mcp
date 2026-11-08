"""Read-only reader for the D-Link DGS-1100 EasySmart web interface (a JavaScript UI over small data files).

Login is POST /cgi/login.cgi with `pass` = MD5 hex of the password; the reply is a script that sets the SessID and Gambit cookies,
which must then be sent on every request. The data pages read here are static-looking files under <root>/DS/ such as QVLAN.js,
which hold `var name=[...];` literals. Logout is POST /cgi/logout.cgi. Only an allow-list of those data files is ever requested.
"""
from __future__ import annotations

import ast
import hashlib
import re

import httpx

from . import webui

ROOT = "/DGS-1100-08_1.10.039/"   # firmware-specific folder, checked at login
DATA_FILES = {
    "system": "Switch.js",
    "vlan": "QVLAN.js",
    "port": "Port.js",
    "mirror": "Mirror.js",
    "pvlan": "PVLAN.js",
    "mgmt_vlan": "MgtVLAN.js",
    "trunk": "trunking.js",
}
PORTS = 8
WRITE_CGIS = {"q_vlan_add_edit.cgi", "q_vlan_del.cgi", "q_vlan_set_pvid.cgi", "set_port.cgi", "PortMirroring.cgi"}
MIRROR_MODES = ["Disable", "Rx", "Tx", "Both"]  # ds_MirrorEn: 0 off, then 1/2/3 = sniffer mode index + 1
PORT_STATE = ["Down", "Error Disabled", "10M Half", "10M Full", "100M Half", "100M Full", "1000M Full", "1000M Full"]
PORT_SPEED = ["Disable", "Auto", "10M Half", "10M Full", "100M Half", "100M Full", "1000M Full", "1000M Full"]


def parse_js_vars(text: str) -> dict:
    """`var a=[1,'x'];` -> {"a": [1, "x"]}. Anything that is not a plain literal is kept as its raw text."""
    out: dict = {}
    parts = re.split(r"(?m)^var\s+(\w+)\s*=", text)
    for name, body in zip(parts[1::2], parts[2::2]):
        body = body.strip().rstrip(";").strip()
        try:
            out[name] = ast.literal_eval(body)
        except (ValueError, SyntaxError):
            out[name] = body
    return out


def _members(code: str) -> dict:
    return {"untagged": [i + 1 for i, c in enumerate(code) if c == "U"], "tagged": [i + 1 for i, c in enumerate(code) if c == "T"]}


def build_state(files: dict[str, dict]) -> dict:
    sysf, q, port, mirror = files["system"], files["vlan"], files["port"], files["mirror"]
    ports = []
    for i, (state, speed, flow) in enumerate(port["ds_PortSetting"]):
        ports.append({"port": i + 1, "link": PORT_STATE[state], "speed_set": PORT_SPEED[speed], "flow_control": bool(int(flow)),
                      "pvid": q["ds_PVID"][i]})
    src = mirror.get("ds_MirrSrc", "")
    return {
        "system": {"name": sysf["ds_SysInfo"][0], "location": sysf["ds_SysInfo"][1], "login_timeout_minutes": sysf["ds_SysInfo"][2],
                   "ip": sysf["ds_IPInfo"][1], "netmask": sysf["ds_IPInfo"][2], "gateway": sysf["ds_IPInfo"][3],
                   "uptime": dict(zip(("days", "hours", "minutes", "seconds"), sysf["ds_TimeUp"])),
                   "hardware": sysf["g_DeviceInfo"][0], "serial": sysf["g_DeviceInfo"][1], "mac": sysf["g_DeviceInfo"][2]},
        "ports": ports,
        "vlan": {"enabled_802_1q": q["ds_QVEnable"] == 1,
                 "vlans": [{"vid": v[0], "name": v[1], "membership": v[2], **_members(v[2])} for v in q["ds_QVLANList"]],
                 "management_vlan": {"enabled": q.get("ds_MgtEnable") == 1, "vid": q.get("ds_MgtVID")}},
        "mirror": {"enabled": mirror["ds_MirrorEn"] != 0, "mode": MIRROR_MODES[mirror["ds_MirrorEn"]],
                   "sniffer_port": mirror["ds_MirrTag"] if mirror["ds_MirrorEn"] else None,
                   "source_ports": [i + 1 for i, c in enumerate(src) if c == "1"] if mirror["ds_MirrorEn"] else []},
        "trunk": {"enabled": files["trunk"].get("ds_TrunkStat") == 1, "groups": files["trunk"].get("ds_TrunkList")},
        "port_based_vlan": {"enabled": files["pvlan"].get("ds_PVEnable") == 1, "vlans": files["pvlan"].get("ds_PVLANList")},
        "loop_detection": {"enabled": files["trunk"].get("ds_LBDEn") == 1},
    }


class DLinkSession:
    """One logged-in session; a context manager so that logout always happens."""

    def __init__(self, name: str, switches: dict[str, dict] | None = None, transport: httpx.BaseTransport | None = None):
        self.switches = switches or webui.load_switches()
        if name not in self.switches:
            raise KeyError(f"unknown switch {name!r}; known: {sorted(self.switches)}")
        self.name, self.info = name, self.switches[name]
        if self.info["vendor"] != "dlink":
            raise ValueError(f"{name!r} is not a D-Link switch")
        self.base = f"http://{self.info['host']}"
        self.client = httpx.Client(timeout=10.0, transport=transport or webui.TRANSPORT, headers={"Cookie": "Gambit=login"})
        self.logged_in = False

    def __enter__(self) -> "DLinkSession":
        webui._LOCK.acquire()
        try:
            digest = hashlib.md5(webui.load_password(self.name).encode()).hexdigest()
            r = self.client.post(f"{self.base}/cgi/login.cgi", data={"pass": digest})
            cookies = dict(re.findall(r"document\.cookie='(\w+)=([^;']*);path=/'", r.text))
            if cookies.get("Gambit") in (None, "login"):
                raise PermissionError("login refused (wrong password, or the switch is out of sessions)")
            self.client.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            self.logged_in = True
        except BaseException:
            self.client.close()
            webui._LOCK.release()
            raise
        return self

    def read(self, page: str) -> dict:
        if page not in DATA_FILES:
            raise ValueError(f"page must be one of {sorted(DATA_FILES)}")
        r = self.client.get(f"{self.base}{ROOT}DS/{DATA_FILES[page]}")
        r.raise_for_status()
        if "var " not in r.text:
            raise PermissionError("the switch did not return data (session expired?)")
        return {"page": page, "values": parse_js_vars(r.text)}

    def state(self) -> dict:
        return build_state({p: self.read(p)["values"] for p in DATA_FILES})


    # ---- writes: only these five CGI endpoints can ever be posted -----------------------------------------------
    def _post(self, cgi: str, fields: dict) -> None:
        if cgi not in WRITE_CGIS:
            raise ValueError(f"refusing to post to {cgi!r}")
        r = self.client.post(f"{self.base}/cgi/{cgi}", data=fields)
        r.raise_for_status()

    @staticmethod
    def _check_ports(ports, what):
        if any(not isinstance(p, int) or not 1 <= p <= PORTS for p in ports):
            raise ValueError(f"{what}: ports must be integers 1-{PORTS}")

    def write_vlan(self, vid: int, name: str, untagged: list[int], tagged: list[int]) -> None:
        if not 1 <= vid <= 4094:
            raise ValueError("VID must be 1-4094")
        if len(name) > 10 or not re.fullmatch(r"[A-Za-z0-9_-]*", name):
            raise ValueError("VLAN name: up to 10 letters, digits, - or _")
        self._check_ports(untagged + tagged, "vlan")
        if set(untagged) & set(tagged):
            raise ValueError("a port cannot be both untagged and tagged in one VLAN")
        mem = "".join("U" if p in untagged else "T" if p in tagged else "0" for p in range(1, PORTS + 1))
        fields = {"VID": str(vid), "VName": name, "mem": mem}
        fields.update({f"C{p}": mem[p - 1] for p in range(1, PORTS + 1)})
        self._post("q_vlan_add_edit.cgi", fields)

    def delete_vlan(self, vid: int) -> None:
        if not 2 <= vid <= 4094:
            raise ValueError("VLAN 1 cannot be deleted; VID must be 2-4094")
        self._post("q_vlan_del.cgi", {"tag_id": str(vid), "vname": ""})

    def write_pvids(self, pvids: list[int]) -> None:
        if len(pvids) != PORTS or any(not 1 <= v <= 4094 for v in pvids):
            raise ValueError(f"pvids must be {PORTS} values, each 1-4094")
        self._post("q_vlan_set_pvid.cgi", {"Pvid": [str(v) for v in pvids]})

    def write_port(self, port: int, speed: str, flow: bool) -> None:
        self._check_ports([port], "port")
        if speed not in PORT_SPEED[:7]:
            raise ValueError(f"speed must be one of {PORT_SPEED[:7]}")
        self._post("set_port.cgi", {"port_f": str(port), "port_t": str(port), "speed": str(PORT_SPEED.index(speed)), "flow": "1" if flow else "0"})

    def write_mirror(self, mode: str, sniffer_port: int | None, source_ports: list[int]) -> None:
        if mode not in MIRROR_MODES:
            raise ValueError(f"mode must be one of {MIRROR_MODES}")
        if mode == "Disable":
            self._post("PortMirroring.cgi", {"cEn": "0"})
            return
        self._check_ports(source_ports + [sniffer_port or 0], "mirror")
        if not source_ports or sniffer_port in source_ports:
            raise ValueError("mirroring needs at least one source port, and the sniffer port cannot be a source")
        self._post("PortMirroring.cgi", {"cEn": "1", "sTagP": str(sniffer_port), "sMode": str(MIRROR_MODES.index(mode) - 1),
                                         "rSrcP": [str(p - 1) for p in sorted(set(source_ports))]})

    def __exit__(self, *exc) -> None:
        try:
            if self.logged_in:
                self.client.post(f"{self.base}/cgi/logout.cgi")
        except httpx.HTTPError:
            pass
        finally:
            self.client.close()
            webui._LOCK.release()
