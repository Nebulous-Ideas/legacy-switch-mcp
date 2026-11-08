"""An in-memory D-Link DGS-1100-08 web interface (login, DS data files, five write CGIs) for testing writes."""
import hashlib
import re
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
        self.sysinfo = ["demo-dlk", "", 5]
        self.ipinfo = [0, "198.51.100.3", "255.255.255.0", "198.51.100.1", 7]
        self.reboots = self.resets = 0
        self.restored: bytes | None = None
        self.config_blob = b"DLINK-CONFIG-DEMO"
        self.trunk_stat, self.trunk_list, self.saves = 0, ["00000000", "00000000"], 0
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
        if name == "Switch.js":
            txt = (FIX / "dlink_Switch.js").read_text()
            txt = re.sub(r"var ds_SysInfo=.*?;\n", f"var ds_SysInfo={self.sysinfo};\n", txt)
            return re.sub(r"var ds_IPInfo=.*?;\n", f"var ds_IPInfo={self.ipinfo};\n", txt)
        if name == "trunking.js":
            txt = (FIX / "dlink_trunking.js").read_text()
            lst = ",\n".join(f"[{v}, '{n}', '{m}']" for v, n, m in self.vlans)
            for var, val in (("ds_PortSetting", self.ports), ("ds_PVID", self.pvids), ("ds_TrunkStat", self.trunk_stat),
                             ("ds_TrunkList", self.trunk_list), ("ds_MirrorEn", self.mirror["en"]), ("ds_MirrTag", self.mirror["tag"]),
                             ("ds_MirrSrc", self.mirror["src"]), ("ds_QVLANList", None)):
                lit = f"[{lst}]" if var == "ds_QVLANList" else repr(val)
                txt = re.sub(rf"var {var}=.*?;\n", lambda m, var=var, lit=lit: f"var {var}={lit};\n", txt, count=1, flags=re.S)
            return txt
        f = FIX / f"dlink_{name}"
        return f.read_text() if f.exists() else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        multipart = "multipart" in (request.headers.get("content-type") or "")
        form = {} if multipart else {k: (v if k in ("Pvid", "rSrcP") else v[0]) for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()}
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
            elif name == "backup.cgi":
                if "pswType" not in form:
                    raise httpx.RemoteProtocolError("no answer to a backup request without pswType")   # as on the real DGS-1100-08
                return httpx.Response(200, content=self.config_blob, headers={"content-type": "application/octet-stream; name=config.bin"})
            elif name == "FWBackup.cgi":
                return httpx.Response(200, content=b"FIRMWARE-IMAGE-DEMO" * 100, headers={"content-type": "application/octet-stream; name=firmware.bin"})
            elif name == "changing_pw.cgi":
                if hashlib.md5(form["old_pass"].encode()).hexdigest() == self.digest and form["new_pass"] == form["renew_pass"]:
                    self.digest = hashlib.md5(form["new_pass"].encode()).hexdigest()
            elif name == "set_ip.cgi":
                self.ipinfo = [int(form["dhcp"]), form["ip"], form["submask"], form["gateway"], 7]
            elif name == "reboot.cgi":
                self.reboots += 1
                raise httpx.RemoteProtocolError("the switch rebooted mid-answer")
            elif name == "reset.cgi":
                self.resets += 1
                self.digest = hashlib.md5(b"admin").hexdigest()
                self.ipinfo = [0, "10.90.90.90", "255.0.0.0", "10.90.90.1", 7]
                raise httpx.RemoteProtocolError("the switch reset mid-answer")
            elif name == "restore.cgi":
                self.restored = request.content
            elif name == "set_sys.cgi":
                self.sysinfo = [form["sys"], form["loc"], int(form["SysTimeout"])]
            elif name == "save.cgi":
                self.saves += 1
            elif name == "TrunkingStatus.cgi":
                self.trunk_stat = int(form["enabled_flag"])
            elif name == "TrunkingSetting.cgi":
                if not self.trunk_stat:
                    raise httpx.ReadTimeout("trunking is disabled")   # the real page greys the form out until trunking is enabled
                self.trunk_list[int(form["Group"]) - 1] = form["GroupMember"]
            elif name == "TrunkingDelMems.cgi":
                self.trunk_list[int(form["Group"]) - 1] = "00000000"
            elif name == "PortMirroring.cgi":
                if form["cEn"] == "0":
                    if "sTagP" not in form:   # real DGS-1100-08: a bare cEn=0 never gets an answer (found on hardware)
                        raise httpx.ReadTimeout("switch did not answer a mirror disable without the full form")
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
