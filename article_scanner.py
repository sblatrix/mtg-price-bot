"""
Scanne les flux RSS de sites MTG finance/actus (Draftsim, MTGRocks) et
détecte les articles qui mentionnent une carte suivie ou contiennent des
mots-clés de finance MTG.

Pourquoi RSS plutôt que scraper le HTML : format XML standard, stable,
bien plus fiable que du scraping HTML (leçon apprise avec CardNexus/MTGTop8
plus tôt dans le projet).
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone

import requests

FEEDS = {
    "Draftsim": "https://draftsim.com/category/mtg-news/feed/",
    "MTGRocks": "https://mtgrocks.com/feed/",
}
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"
REQUEST_DELAY = 1.0

FINANCE_KEYWORDS = [
    "spike", "spiking", "reprint", "banned", "unbanned", "restricted",
    "price", "surge", "sold out", "buyout", "investment", "grail",
    "chase", "skyrocket", "tripled", "doubled", "playable", "meta",
]

WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"
CATALOG_PATH = Path(__file__).parent / "product_catalog.json"
AUTO_WATCHLIST_PATH = Path(__file__).parent / "post_release_watchlist.json"
OUTPUT_PATH = Path(__file__).parent / "web_signals.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

MIN_CARD_NAME_LEN = 6


def load_tracked_card_names() -> set[str]:
    names = set()
    if WATCHLIST_PATH.exists():
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        names.update(c["name"] for c in data.get("cards", []))
    if CATALOG_PATH.exists():
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        names.update(v["name"] for v in data.values() if v.get("name"))
    if AUTO_WATCHLIST_PATH.exists():
        names.update(json.loads(AUTO_WATCHLIST_PATH.read_text(encoding="utf-8")))
    return {n for n in names if len(n) >= MIN_CARD_NAME_LEN}


def fetch_rss_items(feed_url: str) -> list[dict]:
    """Parse un flux RSS 2.0 standard (title, link, description, guid, pubDate)."""
    resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "link": link, "description": description, "guid": guid, "pub_date": pub_date})
    return items


def find_matches(text: str, card_names: set[str]) -> tuple[list[str], list[str]]:
    text_lower = text.lower()
    matched_cards = [name for name in card_names if name.lower() in text_lower]
    matched_keywords = [kw for kw in FINANCE_KEYWORDS if kw in text_lower]
    return matched_cards, matched_keywords


def send_web_signal_alert(signal: dict):
    cards_txt = ", ".join(signal["matched_cards"]) if signal["matched_cards"] else "aucune carte suivie citée"
    keywords_txt = ", ".join(signal["matched_keywords"][:5]) if signal["matched_keywords"] else "-"
    message = {
        "content": (
            f"📰 **{signal['source']}** : {signal['title'][:150]}\n"
            f"Cartes suivies mentionnées : {cards_txt} | Mots-clés : {keywords_txt}\n"
            f"{signal['url']}"
        )
    }
    if not DISCORD_WEBHOOK_URL:
        print(f"  [!] {message['content']}")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=message, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Échec envoi Discord : {e}")


def load_existing_signals() -> list[dict]:
    if OUTPUT_PATH.exists():
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    return []


def run():
    card_names = load_tracked_card_names()
    print(f"{len(card_names)} carte(s) suivie(s) à surveiller dans les articles.")

    existing = load_existing_signals()
    seen_ids = {s.get("article_id") for s in existing if s.get("article_id")}
    new_signals = []

    for source_name, feed_url in FEEDS.items():
        print(f"Scan {source_name}...")
        try:
            items = fetch_rss_items(feed_url)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            time.sleep(REQUEST_DELAY)
            continue
        except ET.ParseError as e:
            print(f"  [!] Erreur de parsing XML : {e}")
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  {len(items)} article(s) dans le flux.")

        for item in items:
            article_id = item["guid"]
            if article_id in seen_ids:
                continue

            full_text = f"{item['title']} {item['description']}"
            full_text = re.sub(r"<[^>]+>", " ", full_text)

            matched_cards, matched_keywords = find_matches(full_text, card_names)
            if not matched_cards and not matched_keywords:
                continue

            signal = {
                "article_id": article_id,
                "title": item["title"],
                "url": item["link"],
                "matched_cards": matched_cards,
                "matched_keywords": matched_keywords,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "source": source_name,
            }
            new_signals.append(signal)
            send_web_signal_alert(signal)
            seen_ids.add(article_id)

        time.sleep(REQUEST_DELAY)

    all_signals = (new_signals + existing)[:300]
    OUTPUT_PATH.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(new_signals)} nouveau(x) article(s) pertinent(s) détecté(s).")
    return new_signals


if __name__ == "__main__":
    run()
