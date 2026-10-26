#!/usr/bin/env python3
"""Flux — récupère les dernières vidéos des chaînes de channels.json
et écrit videos.json (sans les Shorts).

Deux modes :
- avec la variable YOUTUBE_API_KEY : API officielle de YouTube (gratuite, fiable depuis GitHub) ;
- sans clé : flux RSS publics (YouTube les refuse parfois aux serveurs).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://www.googleapis.com/youtube/v3/"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
PLAYLIST_FEED = "https://www.youtube.com/feeds/videos.xml?playlist_id={}"
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

def _clean(url):
    """Cache la clé d'API dans les messages d'erreur (les journaux GitHub sont publics)."""
    return re.sub(r"key=[^&\s]+", "key=***", url)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            detail = " — " + str(body["error"]["message"])[:150]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} sur {_clean(url)}{detail}") from None
    except Exception as e:
        raise RuntimeError(f"{e} sur {_clean(url)}") from None


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


# ---------- mode API (avec clé) ----------

def api_get(endpoint, params, key, fetch_fn):
    qs = urllib.parse.urlencode({**params, "key": key})
    return json.loads(fetch_fn(f"{API}{endpoint}?{qs}"))


def resolve_channel_api(entry, key, fetch_fn):
    """Renvoie (identifiant UC..., nom) pour un @handle, une URL ou un identifiant."""
    entry = entry.strip()
    m = re.search(r"(UC[\w-]{22})", entry)
    if m and ("channel/" in entry or entry.startswith("UC")):
        params = {"part": "snippet", "id": m.group(1)}
    else:
        handle = entry.split("youtube.com/", 1)[1] if "youtube.com/" in entry else entry
        handle = handle.split("/")[0].split("?")[0]
        if not handle.startswith("@"):
            handle = "@" + handle
        params = {"part": "snippet", "forHandle": handle}
    items = api_get("channels", params, key, fetch_fn).get("items") or []
    if not items:
        raise ValueError("chaîne introuvable : vérifie le @")
    return items[0]["id"], items[0]["snippet"]["title"]


def fetch_items_api(cid, key, fetch_fn):
    """Dernières vidéos. Renvoie (liste, sans_shorts).

    La liste « UULF » ne contient que les vidéos longues (pas de Shorts).
    Si elle n'existe pas, on prend la liste de tous les envois (« UU »).
    """
    problems = []
    for prefix in ("UULF", "UU"):
        try:
            data = api_get(
                "playlistItems",
                {
                    "part": "snippet,contentDetails",
                    "playlistId": prefix + cid[2:],
                    "maxResults": KEEP_PER_CHANNEL,
                },
                key,
                fetch_fn,
            )
        except Exception as ex:
            problems.append(str(ex))
            continue
        items = []
        for it in data.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            title = ((it.get("snippet") or {}).get("title") or "").strip()
            if not vid or title in ("Private video", "Deleted video"):
                continue
            published = (it["contentDetails"].get("videoPublishedAt")
                         or it["snippet"].get("publishedAt") or "")
            items.append({"id": vid, "title": title,
                          "published": published.replace("Z", "+00:00")})
        return items, prefix == "UULF"
    raise RuntimeError(" | ".join(problems))


# ---------- mode RSS (sans clé) ----------

ID_PATTERNS = (
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
    r'"externalId":"(UC[\w-]{22})"',
    r'"channelId":"(UC[\w-]{22})"',
)


def resolve_channel(entry, fetch_fn):
    """Accepte @handle, URL de chaîne ou identifiant UC... et renvoie l'identifiant UC...

    Essaie le site normal puis le site mobile.
    """
    entry = entry.strip()
    m = re.search(r"(UC[\w-]{22})", entry)
    if m and ("channel/" in entry or entry.startswith("UC")):
        return m.group(1)
    if "youtube.com/" in entry:
        path = entry.split("youtube.com/", 1)[1]
    else:
        path = entry if entry.startswith("@") else "@" + entry

    problems = []
    for host in ("https://www.youtube.com/", "https://m.youtube.com/"):
        try:
            html = fetch_fn(host + path)
        except Exception as ex:
            problems.append(str(ex))
            continue
        for pat in ID_PATTERNS:
            m = re.search(pat, html)
            if m:
                return m.group(1)
        problems.append(f"identifiant absent de {host}{path}")
    raise ValueError(" | ".join(problems))


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


def fetch_items(cid, fetch_fn):
    """Flux de la chaîne ; si YouTube refuse, flux de la liste « vidéos » de la chaîne."""
    urls = [FEED_URL.format(cid)]
    if cid.startswith("UC"):
        urls.append(PLAYLIST_FEED.format("UULF" + cid[2:]))  # vidéos longues
        urls.append(PLAYLIST_FEED.format("UU" + cid[2:]))    # tous les envois
    problems = []
    for url in urls:
        try:
            return parse_feed(fetch_fn(url))
        except Exception as ex:
            problems.append(str(ex))
    raise RuntimeError(" | ".join(problems))


# ---------- programme principal ----------

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run(fetch_fn=fetch, short_fn=is_short, root=ROOT, key=None):
    if key is None:
        key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    channels = load_json(root / "channels.json", {}).get("channels", [])
    out_path = root / "videos.json"
    prev = load_json(out_path, {})
    prev_channels = prev.get("channels", {})
    prev_videos = {v["id"]: v for v in prev.get("videos", [])}

    out_channels, videos, errors = {}, {}, []
    ok = 0

    for entry in channels:
        stage = "identifiant"
        try:
            info = prev_channels.get(entry)
            if key:
                cid, name = (info["id"], info["name"]) if info else \
                    resolve_channel_api(entry, key, fetch_fn)
                stage = "vidéos"
                items, long_only = fetch_items_api(cid, key, fetch_fn)
            else:
                cid = info["id"] if info else resolve_channel(entry, fetch_fn)
                stage = "flux"
                name, items = fetch_items(cid, fetch_fn)
                long_only = False
        except Exception as ex:  # on garde l'ancien état de cette chaîne
            errors.append(f"{entry} [{stage}] : {ex}")
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
                short = False if long_only else short_fn(it["id"])
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
    changed = ok > 0 and payload != old_payload  # si tout a échoué, on n'écrase rien
    if changed:
        payload["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    return changed, errors, ok


def main():
    mode = "API YouTube" if os.environ.get("YOUTUBE_API_KEY", "").strip() else "flux RSS (sans clé)"
    changed, errors, ok = run()
    total = len(load_json(ROOT / "channels.json", {}).get("channels", []))
    print(f"Mode : {mode}")
    print(f"{ok} chaîne(s) sur {total} lue(s)")
    for e in errors:
        print("  ! " + e)
    print("videos.json mis à jour" if changed else "rien de nouveau")
    return 1 if ok == 0 and total > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
