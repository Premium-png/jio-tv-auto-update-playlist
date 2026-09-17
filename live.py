import json
import re
import urllib.request
import datetime

# Configuration
JSON_URL = "https://raw.githubusercontent.com/darkbyteprojects/iptv_png/refs/heads/main/provider_2/live_events.json"
OUTPUT_FILE = "LiveEvent.m3u"


def fetch_json(url):
    """Fetch JSON data from the given URL."""
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())


def build_m3u_header():
    """Build the M3U header with credits and last update timestamp."""
    now = datetime.datetime.now()
    timestamp = now.strftime("%I:%M %p %m-%d-%Y")

    header_lines = [
        "#EXTM3U",
        "#PLAYLIST:Willow Cricket Event Info",
        f"#LAST_UPDATE:{timestamp}",
        "#https://whatsapp.com/channel/0029VbC2oQsC6ZvmwpR3v73v",
        "#Created by - Sayan 10"
    ]
    return "\n".join(header_lines) + "\n"


def parse_url_params(raw_url):
    """
    Split raw URL into clean stream URL and header/query params.
    Handles:
      url|User-Agent=...&Referer=...
      url?md5=...&expires=...|origin=...
      url?|user-agent=...
      url?User-Agent=...&Referer=...
    """
    if "|" in raw_url:
        stream_url, param_string = raw_url.split("|", 1)
    else:
        stream_url, param_string = raw_url, ""

    # If no pipe, but the query string looks like header params, split it.
    if not param_string and "?" in stream_url:
        base, query = stream_url.split("?", 1)
        if re.search(r"(?i)(user-agent|referer|origin)=", query):
            stream_url = base
            param_string = query

    stream_url = stream_url.rstrip("?&")

    params = {}
    if param_string:
        param_string = param_string.lstrip("?")
        for pair in param_string.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key.strip()] = value.strip()

    return stream_url, params


def get_event_name(item):
    """Build a readable event name."""
    info = item.get("eventInfo", {})
    event_name = info.get("eventName") or item.get("title") or "Unknown"

    team_a = info.get("teamA")
    team_b = info.get("teamB")

    if team_a and team_b and team_a != team_b:
        return f"{event_name}: {team_a} vs {team_b}"

    return event_name


def generate_m3u_entry(item, stream):
    """Generate a single M3U entry from one resolved stream."""
    info = item.get("eventInfo", {})

    event_name = get_event_name(item)
    stream_title = stream.get("title", "Unknown")
    name = f"{event_name} - {stream_title}" if stream_title else event_name

    tvg_id = str(item.get("id", item.get("slug", "")))
    category = info.get("eventCat") or item.get("cat") or "Live Events"

    logo = info.get("eventLogo") or item.get("image") or ""
    if logo == "null":
        logo = ""

    raw_url = stream.get("link", "")
    stream_url, params = parse_url_params(raw_url)

    api = stream.get("api", "")
    key_id = ""
    key = ""
    if api and ":" in api:
        key_id, key = api.split(":", 1)

    is_dash = ".mpd" in stream_url.lower()
    is_hls = ".m3u8" in stream_url.lower()

    lines = []

    # EXTINF line
    extinf = (
        f'#EXTINF:-1 tvg-id="{tvg_id}" '
        f'tvg-name="{name}" '
        f'tvg-logo="{logo}" '
        f'group-title="{category}",{name}'
    )
    lines.append(extinf)

    # DASH / HLS properties
    if is_dash:
        lines.append("#KODIPROP:inputstream=inputstream.adaptive")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")

        if key_id and key:
            lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            lines.append(
                f"#KODIPROP:inputstream.adaptive.license_key={key_id}:{key}"
            )

    elif is_hls:
        lines.append("#KODIPROP:inputstream=inputstream.ffmpeg")
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=hls")

    # Headers
    user_agent = (
        params.get("user-agent")
        or params.get("User-Agent")
        or params.get("user_agent")
    )
    if user_agent:
        lines.append(f"#EXTVLCOPT:http-user-agent={user_agent}")

    referer = params.get("Referer") or params.get("referer")
    if referer:
        lines.append(f"#EXTVLCOPT:http-referrer={referer}")

    origin = params.get("Origin") or params.get("origin")
    if origin:
        lines.append(f"#EXTVLCOPT:http-origin={origin}")

    # Final URL
    lines.append(stream_url)

    return "\n".join(lines)


def main():
    try:
        data = fetch_json(JSON_URL)
    except Exception as e:
        print(f"Error fetching JSON: {e}")
        return

    m3u_content = build_m3u_header()
    entry_count = 0

    for item in data:
        for stream in item.get("resolved_streams", []):
            entry = generate_m3u_entry(item, stream)
            m3u_content += entry + "\n\n"
            entry_count += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"Successfully generated {OUTPUT_FILE} with {entry_count} entries.")


if __name__ == "__main__":
    main()
