#!/usr/bin/env python3
"""
Fetch JioTV M3U playlist, normalize every entry so that:
  - the __hdnea__ cookie lives in #EXTHTTP (not in the URL)
  - a #EXTVLCOPT:http-user-agent=... line is present
  - DRM keys stay in #KODIPROP
and write a clean #EXTM3U playlist.
"""

import urllib.request
import urllib.error
import json
import re
from urllib.parse import urlparse, parse_qs, unquote, urlunparse
from datetime import datetime, timezone, timedelta

M3U_URL  = "https://premiumplugx.top/jiostb/mjelo.php?view=raw"
OUT_FILE = "jtvplus2.m3u"
IST      = timezone(timedelta(hours=5, minutes=30))
DEFAULT_UA = "OTT Navigator"


def fetch_playlist(url: str, user_agent: str = DEFAULT_UA) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code}")
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise


def strip_hdnea_from_url(url: str) -> str:
    """Drop __hdnea__ from the query string (cookie now lives in #EXTHTTP)."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("__hdnea__", None)
    new_query = "&".join(f"{k}={v[0]}" for k, v in qs.items())
    return urlunparse(parsed._replace(query=new_query))


def normalize_m3u(m3u_text: str, default_ua: str = DEFAULT_UA) -> str:
    lines = m3u_text.splitlines()
    out = ["#EXTM3U"]
    i, n = 0, len(lines)

    while i < n:
        line = lines[i].rstrip()
        s = line.strip()

        if not s:
            i += 1
            continue

        # Skip any stray existing #EXTM3U (we already emitted one)
        if s.startswith("#EXTM3U"):
            i += 1
            continue

        if not s.startswith("#EXTINF:"):
            out.append(line)
            i += 1
            continue

        # ---- New channel block ----
        extinf = line
        i += 1

        header_lines = []          # KODIPROP and anything else we don't rewrite
        cookie = ""
        ua = ""

        # Collect header lines until the URL
        while i < n and not lines[i].strip().startswith(("http://", "https://")):
            l = lines[i].rstrip()
            t = l.strip()
            if not t:
                i += 1
                continue

            if t.startswith("#EXTHTTP:"):
                payload = t[len("#EXTHTTP:"):].strip()
                try:
                    data = json.loads(payload)
                    cookie = data.get("cookie", "") or cookie
                except json.JSONDecodeError:
                    pass
                i += 1
                continue

            if t.startswith("#EXTVLCOPT:") and "http-user-agent=" in t:
                ua = t.split("http-user-agent=", 1)[1].strip()
                i += 1
                continue

            header_lines.append(l)
            i += 1

        url = lines[i].strip() if i < n else ""
        if i < n:
            i += 1

        # Move cookie out of the URL into #EXTHTTP
        if url:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            if not cookie and "__hdnea__" in qs:
                cookie = unquote(qs["__hdnea__"][0])
            url = strip_hdnea_from_url(url)

        # ---- Emit normalized block ----
        out.append(extinf)
        out.extend(header_lines)

        if cookie:
            out.append(f'#EXTHTTP:{{"cookie": "{cookie}"}}')
        if ua:
            out.append(f"#EXTVLCOPT:http-user-agent={ua}")
        elif default_ua:
            out.append(f"#EXTVLCOPT:http-user-agent={default_ua}")

        if url:
            out.append(url)

    return "\n".join(out) + "\n"


def format_expiry(exp_ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(exp_ts), tz=IST)
    except (ValueError, OSError, TypeError):
        return ""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return (f"{dt.day}/{dt.month}/{dt.year} "
            f"{hour12}:{dt.minute:02d}:{dt.second:02d} {ampm} IST")


def get_cookie_expiry(cookie: str) -> str:
    if not cookie:
        return ""
    m = re.search(r"exp=(\d+)", cookie)
    return format_expiry(m.group(1)) if m else ""


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    print(f"[*] Fetching playlist from {M3U_URL}...")
    m3u = fetch_playlist(M3U_URL)
    print(f"[+] {len(m3u):,} bytes downloaded")

    print("[*] Normalizing M3U...")
    normalized = normalize_m3u(m3u, default_ua=DEFAULT_UA)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(normalized)
    print(f"[+] Saved -> {OUT_FILE}  ({normalized.count('#EXTINF:'):,} entries)")

    # Sample preview
    first = normalized.split("#EXTINF:", 1)
    if len(first) == 2:
        sample = ("#EXTINF:" + first[1]).split("http", 1)
        print("\n[*] First entry preview:")
        print(sample[0].rstrip())
