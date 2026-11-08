"""Read-only reader for the CGI web interface of the USRobotics 7724 (a frameset with cgi_* pages).

Only an allow-list of read pages is ever requested. The password comes from the environment or a 0600 file, never from
a tool argument, and the session is always logged out so the switch's single admin session is not left locked.
"""
from __future__ import annotations

import json
import os
import re
import stat
import threading
from html.parser import HTMLParser
from pathlib import Path

import httpx

READ_PAGES = {
    "status": "cgi_device",
    "port": "cgi_port",
    "vlan": "cgi_vlan",
    "trunk": "cgi_trunk",
    "mirror": "cgi_mirror",
    "statistics": "cgi_statist",
    "system": "cgi_sys",
    "trap": "cgi_trap",
}
CONFIG_DIR = Path(os.environ.get("SWITCH_MCP_CONFIG", Path.home() / ".config" / "switch-mcp"))
# Switches come from ~/.config/switch-mcp/switches.json (see switches.example.json): {"name": {"host": "192.0.2.3", "vendor": "usrobotics"|"dlink"}}
DEFAULT_SWITCHES: dict[str, dict] = {}
TRANSPORT: httpx.BaseTransport | None = None  # tests inject a fake switch here
_LOCK = threading.Lock()  # these switches allow one web session at a time


def load_switches() -> dict[str, dict]:
    switches = dict(DEFAULT_SWITCHES)
    path = CONFIG_DIR / "switches.json"
    if path.exists():
        switches.update(json.loads(path.read_text()))
    return switches


def load_password(name: str) -> str:
    env = "SWITCH_MCP_PASSWORD_" + name.upper().replace("-", "_")
    if os.environ.get(env):
        return os.environ[env]
    path = CONFIG_DIR / "passwords.json"
    if not path.exists():
        raise RuntimeError(f"no password for {name!r}: set {env} or create {path} (mode 600)")
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(f"{path} must not be readable by group or others (chmod 600)")
    passwords = json.loads(path.read_text())
    if name not in passwords:
        raise RuntimeError(f"{path} has no entry for {name!r}")
    return passwords[name]


class _Page(HTMLParser):
    """Parser for the switches' hand-written pages, which never close <td>, <tr> or <option>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.tables: list[list[list[str]]] = []
        self.controls: list[dict] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None
        self._heading: list[str] | None = None
        self._select: dict | None = None
        self._option: dict | None = None
        self._title: list[str] | None = None
        self.title: str | None = None

    def _end_cell(self) -> None:
        if self._cell is not None and self._stack and self._stack[-1]:
            self._stack[-1][-1].append(" ".join("".join(self._cell).split()))
        self._cell = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._end_cell()
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._end_cell()
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._end_cell()
            if not self._stack[-1]:
                self._stack[-1].append([])
            self._cell = []
        elif tag in ("h1", "h2"):
            self._heading = []
        elif tag == "title":
            self._title = []
        elif tag == "select":
            self._select = {"name": a.get("name"), "type": "select", "options": []}
            self.controls.append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = {"value": a.get("value"), "label": "", "selected": "selected" in a}
            self._select["options"].append(self._option)
        elif tag == "input":
            kind = (a.get("type") or "text").lower()
            if kind in ("submit", "button"):
                return
            c = {"name": a.get("name"), "type": kind, "value": "<hidden>" if kind == "password" else a.get("value", "")}
            if kind in ("checkbox", "radio"):
                c["checked"] = "checked" in a
            self.controls.append(c)

    def handle_endtag(self, tag):
        if tag == "table" and self._stack:
            self._end_cell()
            rows = [r for r in self._stack.pop() if any(r)]  # keep empty cells so columns stay aligned
            if rows:
                self.tables.append(rows)
        elif tag in ("h1", "h2") and self._heading is not None:
            text = " ".join("".join(self._heading).split())
            if text:
                self.headings.append(text)
            self._heading = None
        elif tag == "title" and self._title is not None:
            self.title = " ".join("".join(self._title).split()) or None
            self._title = None
        elif tag == "select":
            self._select = self._option = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._title is not None:
            self._title.append(data)
        if self._option is not None:
            self._option["label"] = (self._option["label"] + " " + data).strip()


SPEEDS = {"100M Full": 1, "100M Half": 2, "10M Full": 3, "10M Half": 4, "Auto": 5, "Disable": 6}
QOS = {"Normal": 0, "High": 1}
MIRROR_MODES = {"Disable": "00", "Rx": "01", "Tx": "02", "Both": "03"}
_WRITE_PATHS = re.compile(r"^(cgi_setport=\d{1,2}|cgi_setvlan=\d{1,2}|cgi_mirror)$")


def parse_vlan_groups(page: dict) -> list[dict]:
    table = next(t for t in page["tables"] if t and t[0][:1] == ["ID"] and "Member" in t[0])
    return [{"group": int(r[0]), "description": r[1], "members": sorted(int(x) for x in r[2].split())} for r in table[1:] if len(r) >= 3]


def parse_ports(page: dict) -> list[dict]:
    ports = []
    for table in page["tables"]:
        for r in table:
            if len(r) in (6, 12) and r[0].isdigit():
                for i in range(0, len(r), 6):
                    ports.append({"port": int(r[i]), "speed_set": r[i + 1], "link": r[i + 2], "flow_set": r[i + 3],
                                  "flow_status": r[i + 4], "qos": r[i + 5]})
    return sorted(ports, key=lambda p: p["port"])


def parse_mirror(page: dict) -> dict:
    def chosen(name):
        c = next(c for c in page["controls"] if c["name"] == name)
        o = next((o for o in c["options"] if o["selected"]), c["options"][0])
        return o["label"], o["value"]
    mode, _ = chosen("mode")
    sniffer, sv = chosen("sniffer")
    source, tv = chosen("source")
    port = lambda label, v: None if v == "99" or not label.isdigit() else int(label)
    return {"mode": mode, "sniffer_port": port(sniffer, sv), "source_port": port(source, tv)}


def extract(html: str) -> dict:
    """Turn a page into headings, tables (rows of cell text), and form controls. Key/value tables are also given as a dict."""
    p = _Page()
    p.feed(html)
    p.close()
    result: dict = {"title": p.title, "headings": p.headings, "tables": p.tables, "controls": p.controls}
    for table in p.tables:  # first label/value table only (later two-column tables are lists, not settings)
        if len(table) > 2 and all(len(r) == 2 and r[0] for r in table):
            result["values"] = {r[0]: r[1] for r in table}
            break
    return result


class Session:
    """One logged-in web session. Use as a context manager so logout always happens."""

    def __init__(self, name: str, switches: dict[str, dict] | None = None, transport: httpx.BaseTransport | None = None):
        self.switches = switches or load_switches()
        if name not in self.switches:
            raise KeyError(f"unknown switch {name!r}; known: {sorted(self.switches)}")
        self.name = name
        self.info = self.switches[name]
        if self.info["vendor"] != "usrobotics":
            raise NotImplementedError(f"web reading is only implemented for the USRobotics 7724; {name!r} is {self.info['vendor']}")
        self.base = f"http://{self.info['host']}"
        self.client = httpx.Client(timeout=10.0, follow_redirects=True, transport=transport or TRANSPORT)
        self.logged_in = False

    def _password(self) -> str:
        return load_password(self.name)

    def __enter__(self) -> "Session":
        _LOCK.acquire()
        try:
            r = self.client.post(f"{self.base}/cgi_login", data={"passwd": self._password(), "post_url": "cgi_device."})
            if "name=passwd" in r.text or 'name="passwd"' in r.text:
                raise PermissionError("login refused (wrong password, or another session holds the switch)")
            self.logged_in = True
        except BaseException:
            self.client.close()
            _LOCK.release()
            raise
        return self

    def read(self, page: str) -> dict:
        if page not in READ_PAGES:
            raise ValueError(f"page must be one of {sorted(READ_PAGES)}")
        r = self.client.get(f"{self.base}/{READ_PAGES[page]}")
        r.raise_for_status()
        data = extract(r.text)
        data["page"] = page
        return data


    # ---- parsed state -------------------------------------------------------------------------------------------
    def state(self) -> dict:
        """Parsed vlan groups, port status and mirror setting (three page reads)."""
        return {"vlan": parse_vlan_groups(self.read("vlan")), "ports": parse_ports(self.read("port")),
                "mirror": parse_mirror(self.read("mirror"))}

    # ---- writes: only the three forms below can ever be posted -------------------------------------------------
    def _post(self, path: str, fields: dict) -> None:
        if not _WRITE_PATHS.match(path):
            raise ValueError(f"refusing to post to {path!r}")
        r = self.client.post(f"{self.base}/{path}", data=fields)
        r.raise_for_status()

    def write_port(self, port: int, speed: str, flow: bool, qos: str) -> None:
        if not 1 <= port <= 24:
            raise ValueError("port must be 1-24")
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {list(SPEEDS)}")
        if qos not in QOS:
            raise ValueError(f"qos must be one of {list(QOS)}")
        if flow and speed != "Auto":
            raise ValueError("flow control can only be enabled with speed Auto")
        f = "1" if flow else "0"
        self._post(f"cgi_setport={port - 1}", {"port": str(port - 1), "speed": str(SPEEDS[speed]), "flow": f, "qos": str(QOS[qos]),
                                              "set_flow": f, "set_qos": str(QOS[qos]), "post_url": "cgi_port."})

    def write_vlan(self, group: int, description: str, members: list[int]) -> None:
        if not 1 <= group <= 15:
            raise ValueError("group must be 1-15")
        if not members or any(not 1 <= m <= 24 for m in members):
            raise ValueError("members must be a non-empty list of ports 1-24")
        if len(description) > 8 or not all(c.isalnum() or c in "-_ " for c in description):
            raise ValueError("description: up to 8 letters, digits, space, - or _")
        # measured on the real 7724: an empty desc is IGNORED (the old name stays); a single space clears it
        self._post(f"cgi_setvlan={group - 1}", {"vlan_id": str(group - 1), "desc": description or " ",
                                                "vcb": [f"{m - 1:02d}" for m in sorted(set(members))], "post_url": "cgi_vlan."})

    def write_mirror(self, mode: str, sniffer: int | None, source: int | None) -> None:
        if mode not in MIRROR_MODES:
            raise ValueError(f"mode must be one of {list(MIRROR_MODES)}")
        if mode != "Disable":
            if sniffer is None or source is None or sniffer == source or not (1 <= sniffer <= 24 and 1 <= source <= 24):
                raise ValueError("mirroring needs two different ports, 1-24")
        enc = lambda p: "99" if p is None else f"{p - 1:02d}"
        self._post("cgi_mirror", {"mode": MIRROR_MODES[mode], "sniffer": enc(sniffer), "source": enc(source), "post_url": "cgi_mirror."})

    def __exit__(self, *exc) -> None:
        try:
            if self.logged_in:
                self.client.get(f"{self.base}/cgi_logout")
        except httpx.HTTPError:
            pass
        finally:
            self.client.close()
            _LOCK.release()


def open_session(name: str):
    """The right kind of logged-in session for a switch, by vendor."""
    from . import dlinkweb
    switches = load_switches()
    if name not in switches:
        raise KeyError(f"unknown switch {name!r}; known: {sorted(switches)}")
    return dlinkweb.DLinkSession(name, switches) if switches[name]["vendor"] == "dlink" else Session(name, switches)
