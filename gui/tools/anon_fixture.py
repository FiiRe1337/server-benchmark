"""Anonymise real sbench output for gui/tests/fixtures (no real host names, IPs, user names, MACs, physical hosts).

  python tools/anon_fixture.py <in.json> <out.json>     (UTF-8 BOM of the core's files is kept if present)

Host names -> DEMO-HOST-01 / DEMO-PHYS-01, IPv4 -> 192.0.2.x (RFC 5737), IPv6 -> 2001:db8::x (RFC 3849),
C:\\Users\\<name> -> C:\\Users\\DEMO-USER, random temp dir names -> sbench-demo."""
import json, re, sys

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
IPV6 = re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7}(?:%\d+)?\b", re.I)
USER = re.compile(r"([A-Za-z]:\\Users\\)[^\\]+", re.I)
TMP = re.compile(r"sbench-(smoke|selftest)-[A-Za-z0-9_]+")


def anon(doc):
    m = {}

    def ip4(s):
        return m.setdefault(s, f"192.0.2.{10 + len([k for k in m if '.' in k])}")

    def ip6(s):
        return m.setdefault(s, f"2001:db8::{10 + len([k for k in m if ':' in k])}")

    def walk(o, key=None):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k == "host" and isinstance(v, str):
                    out[k] = "DEMO-HOST-01"
                elif k == "physical_host" and isinstance(v, str):
                    out[k] = "DEMO-PHYS-01"
                elif k in ("mac", "serial", "uuid", "sid") and isinstance(v, str):
                    out[k] = "DEMO-" + k.upper()
                else:
                    out[k] = walk(v, k)
            return out
        if isinstance(o, list):
            return [walk(v, key) for v in o]
        if isinstance(o, str):
            s = USER.sub(r"\1DEMO-USER", o)
            s = TMP.sub("sbench-demo", s)
            s = IPV4.sub(lambda x: ip4(x.group(0)), s)
            if key in ("ipv6",):
                s = IPV6.sub(lambda x: ip6(x.group(0)), s)
            return s
        return o
    return walk(doc)


if __name__ == "__main__":
    raw = open(sys.argv[1], "rb").read(); bom = raw.startswith(b"\xef\xbb\xbf")
    doc = anon(json.loads(raw.decode("utf-8-sig")))
    with open(sys.argv[2], "w", encoding="utf-8-sig" if bom else "utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False); f.write("\n")
