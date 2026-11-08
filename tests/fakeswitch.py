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

    # ---- http
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        if request.method == "POST":
            form = {k: (v if k == "vcb" else v[0]) for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
            if path == "cgi_login":
                self.logged_in = form.get("passwd") == "pw"
                return httpx.Response(200, text="<html>welcome</html>" if self.logged_in else "<form><input type=password name=passwd></form>")
            assert self.logged_in, "write without login"
            self.posts.append((path, form))
            if path.startswith("cgi_setvlan="):
                g = self.groups[int(form["vlan_id"])]
                if form["desc"] != "":  # real 7724: an empty description is ignored; a single space clears it
                    g["desc"] = form["desc"].strip()
                g["members"] = sorted(int(v) + 1 for v in form["vcb"])
            elif path.startswith("cgi_setport="):
                self.ports[int(form["port"]) + 1] = {"speed": SPEEDS[form["speed"]], "flow": form["flow"] == "1", "qos": "High" if form["qos"] == "1" else "Normal"}
            elif path == "cgi_mirror":
                dec = lambda v: None if v == "99" else int(v) + 1
                self.mirror = {"mode": MODES[int(form["mode"])], "sniffer": dec(form["sniffer"]), "source": dec(form["source"])}
            return httpx.Response(200, text="<html>done</html>")
        if path == "cgi_logout":
            self.logouts += 1
            return httpx.Response(200, text="bye")
        pages = {"cgi_vlan": self.vlan_page, "cgi_port": self.port_page, "cgi_mirror": self.mirror_page}
        if path in pages:
            return httpx.Response(200, text=pages[path]())
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)
