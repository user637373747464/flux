#!/usr/bin/env python3
"""Flux — récupère les dernières vidéos des chaînes de channels.json
et écrit videos.json (sans les Shorts).

Aucune clé API : on utilise les flux RSS publics de YouTube.
"""

import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
KEEP_PER_CHANNEL = 15  # nombre de vidéos gardées par chaîne
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1; SOCS=CAI",
}
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


# ---------- réseau ----------

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def is_short(video_id):
    """True si c'est un Short, False sinon, None si on n'a pas pu savoir.

    youtube.com/shorts/ID répond 200 pour un Short,
    et redirige (3xx) vers /watch pour une vidéo normale.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        f"https://www.youtube.com/shorts/{video_id}", headers=HEADERS, method="HEAD"
    )
    try:
        with opener.open(req, timeout=15) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return False
        return None
    except Exception:
        return None


# ---------- analyse ----------

def resolve_channel(entry, fetch_fn):
    """Accepte @handle, URL de chaîne ou identifiant UC... et renvoie l'identifiant UC..."""
    entry = entry.strip()
    m = re.search(r"(UC[\w-]{22})", entry)
    if m and ("channel/" in entry or entry.startswith("UC")):
        return m.group(1)
    if "youtube.com/" in entry:
        url = entry if entry.startswith("http") else "https://" + entry
    else:
        handle = entry if entry.startswith("@") else "@" + entry
        url = f"https://www.youtube.com/{handle}"
    html = fetch_fn(url)
    for pat in (
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
        r'"externalId":"(UC[\w-]{22})"',
        r'"channelId":"(UC[\w-]{22})"',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise ValueError("chaîne introuvable")


def parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    name = root.findtext("a:author/a:name", default="", namespaces=NS)
    items = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        items.append(
            {
                "id": vid,
                "title": (e.findtext("a:title", default="", namespaces=NS) or "").strip(),
                "published": e.findtext("a:published", default="", namespaces=NS),
            }
        )
    return name, items


# ---------- programme principal ----------

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run(fetch_fn=fetch, short_fn=is_short, root=ROOT):
    channels = load_json(root / "channels.json", {}).get("channels", [])
    out_path = root / "videos.json"
    prev = load_json(out_path, {})
    prev_channels = prev.get("channels", {})
    prev_videos = {v["id"]: v for v in prev.get("videos", [])}

    out_channels, videos, errors = {}, {}, []
    ok = 0

    for entry in channels:
        try:
            info = prev_channels.get(entry)
            cid = info["id"] if info else resolve_channel(entry, fetch_fn)
            name, items = parse_feed(fetch_fn(FEED_URL.format(cid)))
        except Exception as ex:  # on garde l'ancien état de cette chaîne
            errors.append(f"{entry} : {ex}")
            old = prev_channels.get(entry)
            if old:
                out_channels[entry] = old
                for v in prev_videos.values():
                    if v.get("channelId") == old["id"]:
                        videos[v["id"]] = v
            continue

        ok += 1
        out_channels[entry] = {"id": cid, "name": name or entry}
        for it in items[:KEEP_PER_CHANNEL]:
            old = prev_videos.get(it["id"])
            short = old.get("short") if old else None
            if short is None:
                short = short_fn(it["id"])
            videos[it["id"]] = {
                "id": it["id"],
                "title": it["title"],
                "channel": name or entry,
                "channelId": cid,
                "published": it["published"],
                "short": short,
            }

    payload = {
        "channels": out_channels,
        "videos": sorted(videos.values(), key=lambda v: v["published"], reverse=True),
    }
    old_payload = {k: prev.get(k) for k in ("channels", "videos")}
    changed = payload != old_payload
    if changed:
        payload["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    return changed, errors, ok


def main():
    changed, errors, ok = run()
    for e in errors:
        print("  ! " + e, file=sys.stderr)
    print("videos.json mis à jour" if changed else "rien de nouveau")
    return 1 if (ok == 0 and errors) else 0


if __name__ == "__main__":
    sys.exit(main())
