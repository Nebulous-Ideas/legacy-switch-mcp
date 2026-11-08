"""An in-memory D-Link DGS-1100-08 web interface (login, DS data files, five write CGIs) for testing writes."""
import hashlib
from pathlib import Path
from urllib.parse import parse_qs

import httpx

FIX = Path(__file__).parent / "fixtures"
ROOT = "/DGS-1100-08_1.10.039/DS/"


class FakeDLink:
    def __init__(self, password="pw"):
        self.digest = hashlib.md5(password.encode()).hexdigest()
        self.vlans = [[1, "", "UUUUUUUU"]]
        self.pvids = [1] * 8
        self.ports = [[7, 1, 0]] * 5 + [[0, 1, 0]] * 3     # [state, speed idx, flow]
        self.mirror = {"en": 0, "tag": 1, "src": "00000000"}
        self.posts: list[tuple[str, dict]] = []
        self.logins = self.logouts = 0
        self.cookie = None

    def _data(self, name):
        if name == "QVLAN.js":
            lst = ",\n".join(f"[{v}, '{n}', '{m}']" for v, n, m in self.vlans)
            return f"var ds_QVEnable=1;\nvar ds_PVID={self.pvids};\nvar ds_QVLANList=[{lst}];\nvar ds_MgtEnable=0;\nvar ds_MgtVID=1;\n"
        if name == "Port.js":
            return "var ds_PortSetting=" + str(self.ports) + ";\nvar ds_TrunkStat=0;\n"
        if name == "Mirror.js":
            return f"var ds_MirrorEn={self.mirror['en']};\nvar ds_MirrTag={self.mirror['tag']};\nvar ds_MirrSrc='{self.mirror['src']}';\n"
        f = FIX / f"dlink_{name}"
        return f.read_text() if f.exists() else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = {k: (v if k in ("Pvid", "rSrcP") else v[0]) for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
        if path == "/cgi/login.cgi":
            self.logins += 1
            if form.get("pass") == self.digest:
                self.cookie = "Gambit=tok123"
                return httpx.Response(200, text="<script>document.cookie='SessID=3;path=/';\ndocument.cookie='Gambit=tok123;path=/';</script>")
            return httpx.Response(200, text="<html>bad</html>")
        if path == "/cgi/logout.cgi":
            self.logouts += 1
            self.cookie = None
            return httpx.Response(200, text="bye")
        if "Gambit=tok123" not in (request.headers.get("cookie") or ""):
            return httpx.Response(200, text="<html>login</html>")
        if request.method == "POST":
            self.posts.append((path, form))
            name = path.rsplit("/", 1)[-1]
            if name == "q_vlan_add_edit.cgi":
                vid = int(form["VID"])
                row = [vid, form["VName"], form["mem"]]
                for i, v in enumerate(self.vlans):
                    if v[0] == vid:
                        self.vlans[i] = row
                        break
                else:
                    self.vlans.append(row)
            elif name == "q_vlan_del.cgi":
                self.vlans = [v for v in self.vlans if v[0] != int(form["tag_id"])]
            elif name == "q_vlan_set_pvid.cgi":
                self.pvids = [int(x) for x in form["Pvid"]]
            elif name == "set_port.cgi":
                i = int(form["port_f"]) - 1
                self.ports[i] = [self.ports[i][0], int(form["speed"]), int(form["flow"])]
            elif name == "PortMirroring.cgi":
                if form["cEn"] == "0":
                    self.mirror = {"en": 0, "tag": 1, "src": "00000000"}
                else:
                    src = "".join("1" if str(i) in form["rSrcP"] else "0" for i in range(8))
                    self.mirror = {"en": int(form["sMode"]) + 1, "tag": int(form["sTagP"]), "src": src}
            return httpx.Response(200, text="<html>ok</html>")
        if path.startswith(ROOT):
            body = self._data(path[len(ROOT):])
            return httpx.Response(200, text=body) if body else httpx.Response(404)
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handle)
