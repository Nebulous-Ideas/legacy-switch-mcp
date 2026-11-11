"""Property tests: the parsers read bytes and HTML that come off the network, so they must never crash on junk.

A parser may reject input with ValueError (parse_reply documents that); anything else escaping is a bug.
"""
import struct

from hypothesis import given, strategies as st

from legacy_switch_mcp import discovery, dlinkweb, webui

@given(st.binary(max_size=600))
def test_parse_reply_never_crashes(buf):
    try:
        out = discovery.parse_reply(buf)
    except ValueError:
        return
    assert isinstance(out, dict) and "mac" in out


@given(st.lists(st.tuples(st.integers(0, 0xFFFF), st.binary(max_size=40)), min_size=1, max_size=12))
def test_parse_reply_tlv_walk_terminates(tlvs):
    body = b"".join(struct.pack(">HH", t, len(v) + 4) + v for t, v in tlvs)
    buf = b"\x00\x02" + bytes(discovery.HEADER_LEN - 2) + body
    assert isinstance(discovery.parse_reply(buf), dict)


@given(st.text(max_size=400))
def test_parse_js_vars_never_crashes(text):
    assert isinstance(dlinkweb.parse_js_vars(text), dict)


@given(st.text(max_size=600))
def test_extract_never_crashes(html):
    page = webui.extract(html)
    assert set(page) >= {"title", "headings", "tables", "controls"}


TAGS = st.sampled_from(["table", "tr", "td", "th", "select", "option", "input", "h1", "form", "b", "br", "/table", "/tr", "/td", "/select"])
CELLS = st.text(alphabet="0123456789,. -abXYZ", max_size=12)


@st.composite
def tag_soup(draw):
    out = []
    for _ in range(draw(st.integers(0, 40))):
        if draw(st.booleans()):
            attrs = draw(st.sampled_from(["", ' name="t1"', ' name="mode" type="text" value="1"', " selected", ' value="99"']))
            out.append(f"<{draw(TAGS)}{attrs}>")
        else:
            out.append(draw(CELLS))
    return "".join(out)


@given(tag_soup())
def test_page_parsers_fail_cleanly_on_tag_soup(html):
    """Wrong-shaped pages may raise StopIteration/KeyError-style lookups today; they must not hang or corrupt, and extract() itself must work."""
    page = webui.extract(html)
    for fn in (webui.parse_vlan_groups, webui.parse_ports, webui.parse_mirror, webui.parse_trunks, webui.parse_system):
        try:
            fn(page)
        except (StopIteration, KeyError, IndexError, ValueError):
            pass


def test_unnamed_control_regression():
    """Found by the property test above: a <select> with no name used to crash parse_trunks (name was None)."""
    page = webui.extract("<select>")
    assert page["controls"][0]["name"] == ""
    assert webui.parse_trunks(page) == []
