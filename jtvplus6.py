#!/usr/bin/env python3

import re
import json
import requests
import urllib.parse


def parse_m3u(m3u_content):
    """Parse M3U content and extract channels."""
    lines = m3u_content.split('\n')
    channels = []
    current = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # ---- EXTINF header ----
        if line.startswith('#EXTINF:'):
            tvg_id = re.search(r'tvg-id="([^"]*)"', line)
            tvg_name = re.search(r'tvg-name="([^"]*)"', line)
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', line)
            group_title = re.search(r'group-title="([^"]*)"', line)

            name_parts = line.split(',')
            channel_name = name_parts[-1].strip() if len(name_parts) > 1 else "Unknown"

            current = {
                'id': tvg_id.group(1) if tvg_id else '',
                'name': tvg_name.group(1) if tvg_name else channel_name,
                'logo': tvg_logo.group(1) if tvg_logo else '',
                'group': group_title.group(1) if group_title else 'Unknown',
                'url': None,
                'license_key': None,
                'user_agent': 'Droovy',
                'referrer': None,
                'origin': None,
                'cookie': None,
                'stream_headers': None,
                'headers': {},
            }
            continue

        if not current:
            continue

        # ---- KODIPROP license ----
        if line.startswith('#KODIPROP:inputstream.adaptive.license_key='):
            license_key = line.split('=', 1)[1].strip()
            if ':' in license_key:
                current['license_key'] = license_key

        # ---- KODIPROP stream_headers (URL-encoded key=value&key=value) ----
        elif line.startswith('#KODIPROP:inputstream.adaptive.stream_headers='):
            raw = line.split('=', 1)[1].strip()
            current['stream_headers'] = raw
            # Unpack so we can mirror them into EXTVLCOPT / EXTHTTP
            try:
                for pair in raw.split('&'):
                    if '=' not in pair:
                        continue
                    k, v = pair.split('=', 1)
                    k_dec = urllib.parse.unquote(k)
                    v_dec = urllib.parse.unquote(v)
                    current['headers'][k_dec] = v_dec
                    lk = k_dec.lower()
                    if lk == 'cookie':
                        current['cookie'] = v_dec
                    elif lk == 'referer':
                        current['referrer'] = v_dec
                    elif lk == 'origin':
                        current['origin'] = v_dec
                    elif lk == 'user-agent':
                        current['user_agent'] = v_dec
            except Exception:
                pass

        # ---- EXTVLCOPT lines ----
        elif line.startswith('#EXTVLCOPT:http-user-agent='):
            current['user_agent'] = line.split('=', 1)[1].strip()

        elif line.startswith('#EXTVLCOPT:http-referrer='):
            current['referrer'] = line.split('=', 1)[1].strip()

        elif line.startswith('#EXTVLCOPT:http-cookie='):
            current['cookie'] = line.split('=', 1)[1].strip()

        # ---- EXTHTTP JSON blob ----
        elif line.startswith('#EXTHTTP:'):
            payload = line[len('#EXTHTTP:'):].strip()
            try:
                hdrs = json.loads(payload)
                if isinstance(hdrs, dict):
                    for k, v in hdrs.items():
                        if v is None:
                            continue
                        current['headers'][k] = str(v)
                        lk = k.lower()
                        if lk == 'cookie':
                            current['cookie'] = str(v)
                        elif lk == 'referer':
                            current['referrer'] = str(v)
                        elif lk == 'origin':
                            current['origin'] = str(v)
                        elif lk == 'user-agent':
                            current['user_agent'] = str(v)
            except (json.JSONDecodeError, ValueError):
                pass

        # ---- Stream URL ----
        elif not line.startswith('#'):
            current['url'] = line
            if current['url']:
                channels.append(current.copy())
            current = {}

    return channels


def _build_headers_dict(channel):
    """Collect all header key/values we know about into one dict."""
    headers = {}

    # Start from anything already collected in headers
    for k, v in (channel.get('headers') or {}).items():
        headers[k] = v

    # Canonical values win
    if channel.get('user_agent'):
        headers['User-Agent'] = channel['user_agent']
    if channel.get('referrer'):
        headers['Referer'] = channel['referrer']
    if channel.get('origin'):
        headers['Origin'] = channel['origin']
    elif channel.get('referrer'):
        # Fall back: Origin == scheme://host of referrer
        parsed = urllib.parse.urlparse(channel['referrer'])
        if parsed.scheme and parsed.netloc:
            headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
    if channel.get('cookie'):
        headers['Cookie'] = channel['cookie']

    return headers


def convert_channel(channel):
    """Convert a single channel to the target (Kodi-friendly) format."""
    lines = []

    # EXTINF
    lines.append(
        f'#EXTINF:-1 tvg-id="{channel["id"]}" tvg-name="{channel["name"]}" '
        f'tvg-logo="{channel["logo"]}" group-title="{channel["group"]}",{channel["name"]}'
    )

    # KODIPROP basics
    lines.append('#KODIPROP:inputstream=inputstream.adaptive')
    lines.append('#KODIPROP:inputstream.adaptive.manifest_type=mpd')

    # License key
    if channel.get('license_key'):
        lines.append('#KODIPROP:inputstream.adaptive.license_type=clearkey')
        lines.append(
            f'#KODIPROP:inputstream.adaptive.license_key={channel["license_key"]}'
        )

    # --- Assemble headers ---
    headers = _build_headers_dict(channel)

    # KODIPROP stream_headers (URL-encoded) — required by ExoPlayer/Kodi/TiviMate
    if headers:
        sh = '&'.join(
            f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in headers.items()
        )
        lines.append(f'#KODIPROP:inputstream.adaptive.stream_headers={sh}')

    # EXTVLCOPT lines — required by VLC
    if channel.get('user_agent'):
        lines.append(f'#EXTVLCOPT:http-user-agent={channel["user_agent"]}')
    if channel.get('referrer'):
        lines.append(f'#EXTVLCOPT:http-referrer={channel["referrer"]}')
    if channel.get('cookie'):
        lines.append(f'#EXTVLCOPT:http-cookie={channel["cookie"]}')

    # EXTHTTP JSON — some IPTV players read this
    if headers:
        lines.append(f'#EXTHTTP:{json.dumps(headers, ensure_ascii=False)}')

    # --- Clean URL ---
    url = channel.get('url') or ''
    if '|' in url:
        url = url.split('|', 1)[0]

    if '?' in url:
        base_url, params = url.split('?', 1)
        keep = [
            p for p in params.split('&')
            if not p.startswith(('User-Agent=', 'Cookie=', 'Referer=', 'Origin='))
        ]
        url = f"{base_url}?{'&'.join(keep)}" if keep else base_url

    lines.append(url)
    return '\n'.join(lines) + '\n\n'


def generate_converted_m3u():
    m3u_url = "https://raw.githubusercontent.com/sixpg/zeyo-test/refs/heads/main/jtv.m3u"

    print("=" * 60)
    print("M3U to Kodi Format Converter")
    print("=" * 60)

    try:
        print(f"\n[*] Downloading M3U: {m3u_url}")
        response = requests.get(m3u_url, timeout=30)
        response.raise_for_status()
        m3u_content = response.text
        print(f"[+] Downloaded {len(m3u_content)} bytes")

        print("\n[*] Parsing M3U...")
        channels = parse_m3u(m3u_content)
        print(f"[+] Found {len(channels)} channels")

        if channels:
            print("\n[*] Sample channel:")
            s = channels[0]
            print(f"  ID:         {s['id']}")
            print(f"  Name:       {s['name']}")
            print(f"  License:    {s.get('license_key', 'None')}")
            print(f"  User-Agent: {s.get('user_agent', 'None')}")
            print(f"  Referrer:   {s.get('referrer', 'None')}")
            print(f"  Cookie:     {(s.get('cookie') or 'None')[:60]}...")
            print(f"  URL:        {s.get('url', '')[:80]}...")

        print("\n[*] Converting channels...")
        output_file = "jtvplus6.m3u"

        with open(output_file, "w", encoding="utf-8") as f:
            f.write('#EXTM3U\n\n')

            converted = 0
            skipped = 0
            for ch in channels:
                try:
                    block = convert_channel(ch)
                    f.write(block)
                    converted += 1
                except Exception as e:
                    skipped += 1
                    print(f"  [-] Error converting {ch.get('name', 'Unknown')}: {e}")

        print(f"\n[+] Successfully converted {converted} channels")
        if skipped:
            print(f"[!] Skipped {skipped} channels due to errors")
        print(f"[+] Output saved to: {output_file}")
        print("=" * 60)

    except Exception as e:
        print(f"\n[-] Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    generate_converted_m3u()
