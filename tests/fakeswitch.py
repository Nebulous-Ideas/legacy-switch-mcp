"""An in-memory USRobotics 7724 web interface, in the same unclosed-tag style as the real pages, for testing writes."""
from urllib.parse import parse_qs

import httpx

SPEEDS = {"1": "100M Full", "2": "100M Half", "3": "10M Full", "4": "10M Half", "5": "Auto", "6": "Disable"}
MODES = ["Disable", "Rx", "Tx", "Both"]


class FakeSwitch:
    def __init__(self, up_ports=(1, 2, 3, 4)):
        self.groups = [{"desc": "Default" if i == 0 else str(i + 1), "members": list(range(1, 25))} for i in range(15)]
        self.ports = {p: {"speed": "Auto", "flow": False, "qos": "Normal"} for p in range(1, 25)}
        self.up = set(up_ports)
        self.mirror = {"mode": "Disable", "sniffer": None, "source": None}
        self.trunk = {"t0": "00", "t1": "00", "t2": "00"}
        self.sys = {"sys": "demo-usr", "loc": "", "login": "5", "ip": ["192", "0", "2", "3"], "mk": ["255", "255", "255", "0"], "gw": ["192", "0", "2", "1"]}
        self.password = "pw"
        self.config_blob = b"USR-CONFIG-DEMO"
        self.restored: bytes | None = None
        self.resets = 0
        self.posts: list[tuple[str, dict]] = []
        self.logged_in = False
        self.logouts = 0

    # ---- pages
    def vlan_page(self):
        rows = "".join(f"<tr>\n <td align=center><a href=cgi_setvlan={i}><b>{i + 1:02d}\n <td>{g['desc'] or '&nbsp;'}\n <td>{' '.join(f'{m:02d}' for m in g['members'])} \n\n"
                       for i, g in enumerate(self.groups))
        return ("<html><table width=90%><td><h1>Port-based VLAN</h1></table><table border=1 width=90%>\n<tr align=center>\n <td width=30><b>ID\n"
                " <td width=80><b>Description\n <td><b>Member\n\n" + rows + "</table></html>")

    def port_page(self):
        def cells(p):
            d = self.ports[p]
            return (f" <td align=center><a href='cgi_setport={p - 1}'><b>{p:02d}\n <td>{d['speed']}\n <td>{'1G Full' if p in self.up else 'Down'}\n"
                    f" <td>{'Enable' if d['flow'] else 'Disable'}\n <td>Off\n <td>{d['qos']}\n")
        rows = "".join("<tr>\n" + cells(p) + cells(p + 1) + "\n" for p in range(1, 25, 2))
        return "<html><h1>PORT Status</h1><table border=1 width=700>\n<tr align=center>\n <td><b>ID\n <td><b>Speed\n\n" + rows + "</table></html>"

    def mirror_page(self):
        def sel(name, cur):
            opts = "<option value=99>\n" + "".join(f"<option value={p - 1:02d} {'selected' if cur == p else ''}>{p:02d}\n" for p in range(1, 25))
            return f"<select name={name}>\n{opts}</select>"
        modes = "".join(f"<option value={i:02d} {'selected' if m == self.mirror['mode'] else ''}>{m}\n" for i, m in enumerate(MODES))
        return (f"<html><h1>Mirror Setting</h1><form name=set_mirr><table><tr><td><b>Sniffer Mode<td><select name=mode>{modes}</select>"
                f"<tr><td><b>Sniffer Port<td>{sel('sniffer', self.mirror['sniffer'])}<tr><td><b>Source Port<td>{sel('source', self.mirror['source'])}"
                "</table></form></html>")

    TRUNK_OPTIONS = {"t0": [("00", "Disable"), ("01", "01,02"), ("02", "01,02,03,04")], "t1": [("00", "Disable"), ("05", "09,10"), ("06", "09,10,11,12")],
                     "t2": [("00", "Disable"), ("09", "17,18"), ("10", "17,18,19,20")]}

    def trunk_page(self):
        rows = ""
        for i, (name, opts) in enumerate(self.TRUNK_OPTIONS.items()):
            o = "".join(f"<option value={v} {'selected' if self.trunk[name] == v else ''}>{label}\n" for v, label in opts)
            rows += f"<tr>\n <td align=center><b>{i + 1:02d}\n <td><select name={name}>\n{o}"
        return ("<html><h1>Trunk Setting</h1><form method=POST><table border=1 width=190>\n<tr align=center>\n <td width=30><b>ID\n <td><b>Member\n" + rows +
                "</table><input type=hidden name=post_url value=cgi_trunk.></form></html>")

    def system_page(self):
        box = lambda name, vals: "".join(f"<input type=text name={name} value={v} size=3>" for v in vals)
        t = self.sys
        return ("<html><h1>System Setting</h1><form method=POST><table><tr><td>System Name<td><input type=text name=sys value=\"" + t["sys"] + "\">"
                "<tr><td>Location Name<td><input type=text name=loc value=\"" + t["loc"] + "\">"
                "<tr><td>Login Timeout (3 - 30 Minutes)<td><input type=text name=login value=" + t["login"] + ">"
                "<tr><td>IP Address<td>" + box("ip", t["ip"]) + "<tr><td>Subnet Mask<td>" + box("mk", t["mk"]) + "<tr><td>Gateway<td>" + box("gw", t["gw"]) +
                "</table><input type=hidden name=post_url value=cgi_sys.></form></html>")

    # ---- http
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if request.method == "POST":
            multipart = "multipart" in (request.headers.get("content-type") or "")
            form = {} if multipart else {k: (v if k in ("vcb", "ip", "mk", "gw") else v[0]) for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            if path == "cgi_login":
                self.logged_in = form.get("passwd") == self.password
                # real 7724: the reply to a wrong password has NO login form; the form only shows up on the next page request
                return httpx.Response(200, text="<html>welcome</html>" if self.logged_in else "<html>wrong</html>")
            assert self.logged_in, "write without login"
            self.posts.append((path, form))
            if path.startswith("cgi_setvlan="):
                g = self.groups[int(form["vlan_id"])]
                if form["desc"] != "":  # real 7724: an empty description is ignored; a single space clears it
                    g["desc"] = form["desc"].strip()
                g["members"] = sorted(int(v) + 1 for v in form["vcb"])
            elif path.startswith("cgi_setport="):
                self.ports[int(form["port"]) + 1] = {"speed": SPEEDS[form["speed"]], "flow": form["flow"] == "1", "qos": "High" if form["qos"] == "1" else "Normal"}
            elif path == "cgi_pass":
                if form["old_pass"] == self.password and form["new_pass"] == form["renew_pass"]:
                    self.password = form["new_pass"]
            elif path == "cgi_reset":
                self.resets += 1
                self.password = "admin"
                self.sys["ip"], self.sys["mk"], self.sys["gw"] = ["192", "168", "0", "1"], ["255", "255", "255", "0"], ["192", "168", "0", "254"]
                raise httpx.RemoteProtocolError("the switch reset mid-answer")
            elif path == "cgi_backup":
                self.restored = request.content
            elif path == "cgi_trunk":
                self.trunk = {k: form[k] for k in ("t0", "t1", "t2")}
            elif path == "cgi_sys":
                self.sys = {k: form[k] for k in ("sys", "loc", "login", "ip", "mk", "gw")}
            elif path == "cgi_mirror":
                dec = lambda v: None if v == "99" else int(v) + 1
                self.mirror = {"mode": MODES[int(form["mode"])], "sniffer": dec(form["sniffer"]), "source": dec(form["source"])}
            return httpx.Response(200, text="<html>done</html>")
        if path == "config.bin":
            return httpx.Response(200, content=self.config_blob, headers={"content-type": "application/octet-stream"}) if self.logged_in else httpx.Response(200, text="<html>login</html>")
        if path == "cgi_logout":
            self.logouts += 1
            return httpx.Response(200, text="bye")
        if path == "cgi_device":
            return httpx.Response(200, text="<html><h1>Device Status</h1><table><tr><td>Model<td>demo</table></html>" if self.logged_in
                                  else "<form><input type=password name=passwd></form>")
        pages = {"cgi_vlan": self.vlan_page, "cgi_port": self.port_page, "cgi_mirror": self.mirror_page, "cgi_trunk": self.trunk_page, "cgi_sys": self.system_page}
        if path in pages:
            return httpx.Response(200, text=pages[path]())
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
