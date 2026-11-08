# Protocols of the Cameo-built smart switches

Notes from reverse engineering the D-Link DGS-1100-08 (EasySmart, firmware 1.10.039) and the USRobotics 7724 (firmware 2.00.08).
Both are built by Cameo Communications; the vendors' Windows utilities share one code base (`cameo_utility_3` appears in the
USRobotics build's source paths). Everything here was observed on real devices or read from the vendors' own utilities and web pages.

## UDP discovery (port 64515)
- Request, 40 bytes, sent twice: broadcast `255.255.255.255:64515` from source port 64513 (flag `0001`), and multicast
  `239.255.255.100:64515` from source port 64519 (flag `0002`). Sending to one switch's IP also works, across subnets.
- Layout: `0002 0000`, 8 zero bytes, `0001 0000`, **a 4-byte per-OEM id**, the sender's MAC (6 bytes, may be zero), the flag, 12 zero bytes.
- OEM ids seen: D-Link `0x01945ae0`, USRobotics `0x01945ad8`. A switch silently ignores a request carrying another vendor's id.
  Each id sits in its vendor's utility as `c7 82 .. .. .. 00 <imm32>` (mov dword ptr [reg+disp32], imm32).
- Reply: broadcast from port 64515 to `255.255.255.255:64513`, a 40-byte header (`0002 0001`, switch MAC, echo of the request), then TLVs
  (type u16, length u16 including the 4-byte header): 1 model, 2 name, 3 location, 4 IP, 5 netmask, 6 gateway, 7 trap IP, 0x0b firmware.
- Traps: UDP from the switch's port 64515 to port 64514 of the configured trap host (44-byte header, then text).
- The vendors' utilities also set IP/name/password and upgrade firmware (TFTP) over this channel; those exchanges are NOT captured or implemented here.

## USRobotics 7724 web interface (HTTP, port 80)
- Frameset of CGI pages, HTML 3-style with unclosed `<td>`/`<tr>`/`<option>` tags. Login: `POST /cgi_login` with `passwd`; one admin session at a time;
  always `GET /cgi_logout`.
- Reads: `cgi_device`, `cgi_port`, `cgi_vlan`, `cgi_trunk`, `cgi_mirror`, `cgi_statist`, `cgi_sys`, `cgi_trap`.
- Writes (POST): `cgi_setvlan=N` (`vlan_id`, `desc`, repeated `vcb` = 0-based ports, `post_url`), `cgi_setport=N` (`port`, `speed` 1-6, `flow`, `qos`,
  `set_flow`, `set_qos`), `cgi_mirror` (`mode` 00-03, `sniffer`, `source`; 99 = none).
- **Port-based VLANs only**: 15 groups, factory state has all 24 ports in every group. It cannot tag or untag 802.1Q; tags pass through untouched.
  Two ports only separate if EVERY group holding both is changed.
- Quirk: an empty VLAN description is ignored (the old name stays); a single space clears it.

## D-Link DGS-1100 web interface (HTTP, port 80)
- JavaScript UI. Login: `POST /cgi/login.cgi` with `pass` = MD5 hex of the password. The reply is a script that sets the `SessID` and `Gambit` cookies;
  send both on every request. Logout: `POST /cgi/logout.cgi`.
- Data is in `<firmware-folder>/DS/*.js` as `var name=[...];` literals: `Switch.js`, `QVLAN.js`, `Port.js`, `Mirror.js`, `PVLAN.js`, `MgtVLAN.js`, `trunking.js`.
- Writes (POST under `/cgi/`): `q_vlan_add_edit.cgi` (`VID`, `VName`, `mem` = one of U/T/0 per port, `C1..Cn`), `q_vlan_del.cgi` (`tag_id`),
  `q_vlan_set_pvid.cgi` (`Pvid` x ports), `set_port.cgi` (`port_f`, `port_t`, `speed` index, `flow`), `PortMirroring.cgi` (`cEn`, `sTagP`, `sMode`, `rSrcP`).
- Supports 802.1Q. The page's JavaScript enforces rules a client must repeat: a port is untagged in only one VLAN, a PVID must be a VLAN the port is in.
- Verified on hardware: VLAN add and delete. Not yet verified on hardware: PVID, port and mirror writes.
