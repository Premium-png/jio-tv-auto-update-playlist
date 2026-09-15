#!/usr/bin/env python3
# jtv.py — JioTV playlist generator with per-channel cookie selection

import json
import os
import sys
import time
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CHANNELS_URL = (
    "https://jiotv.data.cdn.jio.com/apis/v1.3/getMobileChannelList/get/"
    "?os=android&devicetype=phone&usertype=JIO&version=220&langId=6"
)

# >>> REPLACE WITH YOUR ORIGINAL if different
SPORTS_URL = (
    "https://jiotv.data.cdn.jio.com/apis/v1.3/getSportsChannelList/get/"
    "?os=android&devicetype=phone&usertype=JIO&version=220&langId=6"
)

M3U_FILE = "jtv.m3u"
JSON_FILE = "jtv.json"

HEADERS = {
    "User-Agent": "okhttp/3.12.13",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# GitHub upload (optional) — set these env vars in Actions if you want API push
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")  # e.g. "user/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_REF_NAME", "main")

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------

def get_json(url, headers=None, retries=MAX_RETRIES):
    """GET a URL and return parsed JSON. Retries on transient failures."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers or HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            print(f"[WARN] GET {url} failed (attempt {attempt}/{retries}): {e}",
                  file=sys.stderr)
            if attempt < retries:
                time.sleep(2 * attempt)
    print(f"[ERROR] Giving up on {url}: {last_err}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# COOKIES
# ---------------------------------------------------------------------------

def get_normal_cookie():
    """
    Obtain the standard JioTV auth cookie.
    >>> REPLACE WITH YOUR ORIGINAL implementation if different.

    A common approach: log in via the Jio auth endpoint using credentials from
    env vars, then return the resulting cookie string like:
        "userId=...; sessionId=...; ..."
    """
    # Example: use a pre-provisioned cookie from env (simplest, most reliable)
    cookie = os.environ.get("JIO_COOKIE")
    if cookie:
        return cookie

    # Fallback: try to fetch an anonymous/guest cookie from the API
    # (may not work in all regions — swap with your real login flow)
    try:
        data = get_json(
            "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp?langId=6",
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        if isinstance(data, dict):
            return data.get("cookie") or data.get("ssoToken")
    except Exception as e:
        print(f"[WARN] get_normal_cookie fallback failed: {e}", file=sys.stderr)

    return None


def get_sports_data():
    """
    Fetch sports-channel cookie payload.
    Returns a dict, e.g. {"sportsCookies": [{"name": "...", "cookie": "..."}]}
    >>> REPLACE WITH YOUR ORIGINAL implementation if different.
    """
    data = get_json(SPORTS_URL)
    if not isinstance(data, dict):
        return {}
    return data


def _normalize_sports_cookies(sports_data):
    """
    Normalize sportsCookies to {lowercase_name: cookie}.

    Accepts:
      - {"sportsCookies": [{"name": "...", "cookie": "..."}, ...]}
      - {"sportsCookies": {"name": "cookie", ...}}
      - {"sportsCookies": "single-cookie-string"}   (last resort)
    """
    out = {}
    if not isinstance(sports_data, dict):
        return out

    raw = sports_data.get("sportsCookies")
    if not raw:
        return out

    # Bare string → applies to all sports channels (fallback)
    if isinstance(raw, str):
        out["__all__"] = raw
        return out

    if isinstance(raw, dict):
        items = raw.items()
    else:
        items = (
            (item.get("name"), item.get("cookie"))
            for item in raw
            if isinstance(item, dict)
        )

    for name, cookie in items:
        if name and cookie:
            out[str(name).strip().lower()] = cookie

    return out


# ---------------------------------------------------------------------------
# CHANNEL CLASSIFICATION
# ---------------------------------------------------------------------------

_SPORTS_KEYWORDS = (
    "sports", "sport", "cricket", "football", "soccer",
    "tennis", "badminton", "hockey", "kabaddi", "olympic",
    "star sports", "sony ten", "sony sab", "espn", "eurosport",
    "willow", "dd sports", "sky sports", "nba", "nfl", "f1",
)


def _is_sports_channel(ch):
    """Best-effort detection of a sports channel by name/category/genre."""
    haystack = " ".join(
        str(ch.get(k, ""))
        for k in ("name", "channel_name", "category", "genre", "language")
    ).lower()
    return any(k in haystack for k in _SPORTS_KEYWORDS)


def _pick_cookie(ch, normal_cookie, sports_cookies):
    """
    Return the correct cookie for this channel:
      - sports channel  -> matching sports cookie (exact → fuzzy → __all__),
                           else fall back to normal cookie
      - everything else -> normal cookie
    """
    if _is_sports_channel(ch):
        name = str(ch.get("name") or ch.get("channel_name") or "").strip().lower()

        if name in sports_cookies:
            return sports_cookies[name]

        for sname, scookie in sports_cookies.items():
            if sname == "__all__":
                continue
            if sname and (sname in name or name in sname):
                return scookie

        if "__all__" in sports_cookies:
            return sports_cookies["__all__"]

    return normal_cookie


# ---------------------------------------------------------------------------
# CHANNEL BUILDERS
# ---------------------------------------------------------------------------

def _channel_stream_url(ch, cookie):
    """
    Build the playable URL for a JioTV channel.
    >>> REPLACE WITH YOUR ORIGINAL if your URL template differs.
    """
    # Common pattern — MPD/HLS URL containing the channel id + cookie param
    ch_id = ch.get("channel_id") or ch.get("id")
    return (
        f"https://jiotvcbp.cdn.jio.com/{ch_id}/index.mpd"
        f"?cookie={requests.utils.quote(cookie or '')}"
    )


def create_channel_entry(ch, cookie):
    """Return the M3U block for a single channel."""
    name = ch.get("name") or ch.get("channel_name") or "Unknown"
    ch_id = ch.get("channel_id") or ch.get("id") or ""
    logo = ch.get("logoUrl") or ch.get("logo") or ""
    group = ch.get("category") or ch.get("genre") or "General"
    language = ch.get("language") or ""

    url = _channel_stream_url(ch, cookie)

    return (
        f'#EXTINF:-1 tvg-id="{ch_id}" tvg-logo="{logo}" '
        f'group-title="{group}",{name}\n'
        f'#KODIPROP:inputstream.adaptive.license_type=clearkey\n'
        f'#EXTVLCOPT:http-user-agent=okhttp/3.12.13\n'
        f'#EXTVLCOPT:http-cookie={cookie}\n'
        f'{url}'
    )


def build_channel_object(ch, cookie):
    """Return the JSON object for a single channel."""
    return {
        "id": ch.get("channel_id") or ch.get("id"),
        "name": ch.get("name") or ch.get("channel_name"),
        "logo": ch.get("logoUrl") or ch.get("logo"),
        "category": ch.get("category") or ch.get("genre"),
        "language": ch.get("language"),
        "cookie": cookie,
        "url": _channel_stream_url(ch, cookie),
    }


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def validate(channels, m3u_entries, json_entries):
    """Sanity-check the generated data before writing anything."""
    if not channels:
        print("[ERROR] No channels fetched.", file=sys.stderr)
        sys.exit(1)
    if not m3u_entries:
        print("[ERROR] No M3U entries generated.", file=sys.stderr)
        sys.exit(1)
    if not json_entries:
        print("[ERROR] No JSON entries generated.", file=sys.stderr)
        sys.exit(1)

    # Every entry must carry a cookie
    missing = [e for e in json_entries if not e.get("cookie")]
    if missing:
        print(f"[ERROR] {len(missing)} entries have no cookie.", file=sys.stderr)
        sys.exit(1)

    ratio = len(m3u_entries) / len(channels)
    print(f"[INFO] Generation ratio: {len(m3u_entries)}/{len(channels)} "
          f"({ratio:.1%})")
    if ratio < 0.5:
        print("[ERROR] More than half the channels failed — aborting.",
              file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# GITHUB UPLOAD (optional — the workflow already commits via git)
# ---------------------------------------------------------------------------

def upload_to_github(filename, content):
    """
    Optional: push a file via the GitHub Contents API.
    Skipped silently if GITHUB_TOKEN / GITHUB_REPOSITORY aren't set.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return

    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    # Get existing SHA (needed to update)
    sha = None
    try:
        r = requests.get(api, headers=headers, params={"ref": GITHUB_BRANCH},
                         timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception as e:
        print(f"[WARN] Could not fetch SHA for {filename}: {e}", file=sys.stderr)

    import base64
    payload = {
        "message": f"Update {filename} [skip ci]",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (200, 201):
        print(f"[INFO] GitHub upload OK: {filename}")
    else:
        print(
            f"[ERROR] GitHub upload failed for {filename}: "
            f"{resp.status_code} — {resp.text}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"[START] {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")

    channels = get_json(CHANNELS_URL)
    if isinstance(channels, dict):
        channels = channels.get("channels") or channels.get("data") or []
    if not isinstance(channels, list):
        print("[ERROR] Channels payload is not a list — aborting.", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] Channels loaded: {len(channels)}")

    normal_cookie = get_normal_cookie()
    print(f"[INFO] Normal cookie: {'found' if normal_cookie else 'not found'}")
    if not normal_cookie:
        print("[ERROR] No normal cookie available — aborting.", file=sys.stderr)
        sys.exit(1)

    sports_data = get_sports_data() or {}
    sports_cookies = _normalize_sports_cookies(sports_data)
    print(f"[INFO] Sports cookies loaded: {len(sports_cookies)}")

    m3u_entries = []
    json_entries = []
    sports_hits = 0

    for ch in channels:
        name = ch.get("name") or ch.get("channel_name") or "?"
        try:
            cookie = _pick_cookie(ch, normal_cookie, sports_cookies)
            if cookie and cookie != normal_cookie:
                sports_hits += 1

            m3u_entries.append(create_channel_entry(ch, cookie))
            json_entries.append(build_channel_object(ch, cookie))
        except Exception as e:
            print(f"[WARN] Skipped channel '{name}': {e}", file=sys.stderr)

    print(f"[INFO] Sports channels using sports cookie: {sports_hits}")

    validate(channels, m3u_entries, json_entries)

    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    m3u_content = (
        f'#EXTM3U x-tvg-url="" updated="{timestamp}"\n\n'
        + "\n\n".join(m3u_entries)
    )
    json_content = json.dumps(json_entries, indent=2, ensure_ascii=False)

    # Save locally
    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"[INFO] M3U saved → {M3U_FILE}")

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        f.write(json_content)
    print(f"[INFO] JSON saved → {JSON_FILE}")

    # Optional GitHub API upload (workflow's git push already handles this,
    # so this is only useful if you run jtv.py outside Actions)
    upload_to_github(M3U_FILE, m3u_content)
    upload_to_github(JSON_FILE, json_content)

    print("[DONE] All outputs written successfully.")


if __name__ == "__main__":
    main()
