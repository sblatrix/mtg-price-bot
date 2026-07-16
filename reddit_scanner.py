"""
Scanne plusieurs subreddits MTG via l'API JSON publique de Reddit (gratuite,
sans authentification pour un usage raisonnable) et détecte les posts qui :
- mentionnent une carte qu'on suit déjà (watchlist + catalogue post-sortie)
- OU contiennent des mots-clés de finance MTG (spike, reprint, banned...)

Fragilité connue : l'accès anonyme à l'API JSON de Reddit peut être limité
ou bloqué selon les périodes - à surveiller. Si ce script échoue
systématiquement, il faudra passer par l'API officielle Reddit (OAuth,
gratuite mais plus lourde à mettre en place).
"""
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

SUBREDDITS = ["mtgfinance", "spikes", "ModernMagic", "EDH", "magicTCG"]
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0; +https://github.com/)"
REQUEST_DELAY = 1.5  # Reddit est strict sur le rate-limit anonyme

FINANCE_KEYWORDS = [
    "spike", "spiking", "reprint", "banned", "unbanned", "restricted",
    "price increase", "price jump", "surge", "sold out", "buyout",
    "investment", "grail", "chase", "skyrocket", "tripled", "doubled",
]

WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"
CATALOG_PATH = Path(__file__).parent / "product_catalog.json"
AUTO_WATCHLIST_PATH = Path(__file__).parent / "post_release_watchlist.json"
OUTPUT_PATH = Path(__file__).parent / "web_signals.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

MIN_CARD_NAME_LEN = 6  # évite les faux positifs sur des noms de carte trop courts/génériques


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


def fetch_subreddit_new(subreddit: str, limit: int = 25) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    resp = requests.get(url, params={"limit": limit}, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return [child["data"] for child in data.get("data", {}).get("children", [])]


def find_matches(text: str, card_names: set[str]) -> tuple[list[str], list[str]]:
    text_lower = text.lower()
    matched_cards = [name for name in card_names if name.lower() in text_lower]
    matched_keywords = [kw for kw in FINANCE_KEYWORDS if kw in text_lower]
    return matched_cards, matched_keywords


def send_web_signal_alert(signal: dict):
    cards_txt = ", ".join(signal["matched_cards"]) if signal["matched_cards"] else "aucune carte suivie citée"
    keywords_txt = ", ".join(signal["matched_keywords"]) if signal["matched_keywords"] else "-"
    message = {
        "content": (
            f"💬 **r/{signal['subreddit']}** : {signal['title'][:150]}\n"
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
    print(f"{len(card_names)} carte(s) suivie(s) à surveiller dans les posts.")

    existing = load_existing_signals()
    seen_ids = {s.get("reddit_id") for s in existing if s.get("reddit_id")}
    new_signals = []

    for subreddit in SUBREDDITS:
        print(f"Scan r/{subreddit}...")
        try:
            posts = fetch_subreddit_new(subreddit)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            time.sleep(REQUEST_DELAY)
            continue

        for post in posts:
            post_id = post.get("id")
            if post_id in seen_ids:
                continue

            title = post.get("title", "")
            selftext = post.get("selftext", "")
            full_text = f"{title} {selftext}"

            matched_cards, matched_keywords = find_matches(full_text, card_names)
            if not matched_cards and not matched_keywords:
                continue

            signal = {
                "reddit_id": post_id,
                "subreddit": subreddit,
                "title": title,
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "matched_cards": matched_cards,
                "matched_keywords": matched_keywords,
                "score": post.get("score", 0),
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "source": "Reddit",
            }
            new_signals.append(signal)
            send_web_signal_alert(signal)
            seen_ids.add(post_id)

        time.sleep(REQUEST_DELAY)

    all_signals = (new_signals + existing)[:300]
    OUTPUT_PATH.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(new_signals)} nouveau(x) signal(aux) Reddit détecté(s).")
    return new_signals


if __name__ == "__main__":
    run()
