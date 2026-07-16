"""
Scanne les résultats de tournois récents sur MTGTop8 (standings + decklists
complètes en HTML statique, contrairement à MTGGoldfish dont les decklists
se chargent en JS). Détecte les rares/mythiques qui performent (apparaissent
dans des decks classés) sans être encore largement adoptées dans le méta
global (cf. discussion projet : c'est le signal précoce qu'on veut, pas
juste "combien de decks la jouent déjà").

Structure scrapée (vérifiée sur du HTML brut) :
- /format?f={code}         -> liste d'événements récents (liens event?e=ID)
- /event?e={id}&f={code}   -> standings de l'événement (liens vers chaque deck, event?e=ID&d=DECK_ID)
- /event?e={id}&d={deck_id}&f={code} -> decklist complète (lignes "N Nom de carte")

Fragilité connue : scraping HTML, pas d'API officielle. Respecte le
robots.txt du site (vérifié au démarrage) et un délai entre requêtes.

Volume : à faire tourner 1x/jour maximum, pas toutes les heures - ce script
fait potentiellement des dizaines de requêtes par run.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mtgtop8.com"
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"
REQUEST_DELAY = 0.5

FORMAT_CODES = {
    "standard": "ST",
    "modern": "MO",
    "pioneer": "PI",
    "legacy": "LE",
    "commander": "EDH",  # Duel Commander - le seul format Commander compétitif suivi par MTGTop8
}

MAX_EVENTS_PER_FORMAT = 8
MAX_DECKS_PER_EVENT = 8

CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+)$")
SECTION_HEADERS_RE = re.compile(r"^(LANDS?|CREATURES?|INSTANTS? AND SORC\.?|OTHER SPELLS|SIDEBOARD|PLANESWALKERS?|ARTIFACTS?|ENCHANTMENTS?)\b", re.IGNORECASE)

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

TOURNAMENT_SIGNALS_PATH = Path(__file__).parent / "tournament_signals.json"
RARITY_CACHE_PATH = Path(__file__).parent / "rarity_cache.json"
PERFORMANCE_SIGNALS_PATH = Path(__file__).parent / "performance_signals.json"

# Seuils de détection : apparitions minimum dans des decks classés, et
# % d'adoption globale maximum pour être considérée "sous le radar"
MIN_APPEARANCES = 2
MAX_BASELINE_PCT = 15


def check_robots_allowed() -> bool:
    """Vérifie le robots.txt avant de scraper quoi que ce soit."""
    try:
        resp = requests.get(f"{BASE_URL}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
        if resp.status_code != 200:
            return True  # pas de robots.txt = pas de restriction connue
        text = resp.text.lower()
        # vérification simple : si /event ou /format sont explicitement disallow, on s'arrête
        if "disallow: /event" in text or "disallow: /format" in text:
            print("[!] robots.txt interdit l'accès à /event ou /format. Scan annulé.")
            return False
        return True
    except requests.RequestException:
        print("[!] Impossible de vérifier robots.txt, on continue par défaut.")
        return True


def get_recent_event_ids(format_code: str, limit: int = MAX_EVENTS_PER_FORMAT) -> list[str]:
    url = f"{BASE_URL}/format?f={format_code}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    ids = []
    seen = set()
    for link in soup.find_all("a", href=True):
        m = re.search(r"event\?e=(\d+)", link["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
        if len(ids) >= limit:
            break
    return ids


def get_event_deck_ids(event_id: str, format_code: str) -> list[tuple[str, str]]:
    """Retourne [(deck_id, archetype_name), ...] pour les decks classés de l'événement.

    Parse les paramètres de l'URL proprement (via urlparse/parse_qs) plutôt
    que par regex sur l'ordre exact des paramètres - plus robuste si le site
    ne les met pas toujours dans le même ordre (e=...&d=... vs e=...&f=...&d=...)."""
    url = f"{BASE_URL}/event?e={event_id}&f={format_code}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    decks = []
    seen = set()
    all_hrefs_sample = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "d=" in href or "/deck" in href:
            all_hrefs_sample.append(href)

        qs = parse_qs(urlparse(href).query)
        d_vals = qs.get("d")
        if not d_vals:
            continue
        e_vals = qs.get("e")
        if e_vals and e_vals[0] != str(event_id):
            continue  # lien vers un deck d'un AUTRE événement (pub, "voir aussi"...)

        deck_id = d_vals[0]
        name = link.get_text(strip=True)
        if deck_id not in seen and name:
            seen.add(deck_id)
            decks.append((deck_id, name))

    if not decks and all_hrefs_sample:
        print(f"      [diagnostic] 0 deck matché mais {len(all_hrefs_sample)} lien(s) contenant 'd=' vus, exemples : "
              f"{all_hrefs_sample[:3]}")

    return decks[:MAX_DECKS_PER_EVENT]


def get_deck_cards(event_id: str, deck_id: str, format_code: str) -> list[str]:
    """Récupère la liste des cartes (deck principal + sideboard) d'une decklist."""
    url = f"{BASE_URL}/event?e={event_id}&d={deck_id}&f={format_code}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]

    cards = []
    for line in lines:
        if SECTION_HEADERS_RE.match(line):
            continue
        m = CARD_LINE_RE.match(line)
        if m:
            count, name = int(m.group(1)), m.group(2).strip()
            # filtre grossier : évite de capturer des faux positifs (prix, stats...)
            if 0 < count <= 4 and len(name) > 2 and not name.isdigit():
                cards.append(name)
    return cards


def get_card_rarity(card_name: str, cache: dict) -> str | None:
    """Vérifie le cache d'abord (rempli par tournament_scanner.py), sinon interroge
    Scryfall directement - important : les cartes qu'on cherche ici sont justement
    souvent absentes du cache (pas encore dans le top 50 MTGGoldfish)."""
    cache_key = next((k for k in cache if k.startswith(f"{card_name}|")), None)
    if cache_key:
        return cache[cache_key].get("rarity")

    try:
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": card_name},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        rarity = resp.json().get("rarity")
        cache[f"{card_name}|"] = {"rarity": rarity, "set": None}
        return rarity
    except requests.RequestException:
        return None
    finally:
        time.sleep(0.1)


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def get_baseline_pct(card_name: str, format_name: str, tournament_signals: dict) -> int | None:
    entries = tournament_signals.get(format_name, {}).get("entries", [])
    for e in entries:
        if e["name"] == card_name:
            return e["pct_of_decks"]
    return None


def send_performance_alert(signal: dict):
    baseline = f"{signal['baseline_pct']}%" if signal["baseline_pct"] is not None else "absente du top 50"
    message = {
        "content": (
            f"🚀 **{signal['name']}** ({signal['rarity']}) performe en **{signal['format']}** "
            f"sans être encore adoptée\n"
            f"Vue dans {signal['appearances']} deck(s) classé(s) récemment · "
            f"Adoption globale actuelle : {baseline}"
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


def run_scan():
    if not check_robots_allowed():
        return

    tournament_signals = load_json(TOURNAMENT_SIGNALS_PATH)
    rarity_cache = load_json(RARITY_CACHE_PATH)
    signals = []

    for format_name, format_code in FORMAT_CODES.items():
        print(f"Scan MTGTop8 : {format_name}...")
        card_appearances = {}

        try:
            event_ids = get_recent_event_ids(format_code)
        except requests.RequestException as e:
            print(f"  [!] Erreur récupération événements : {e}")
            continue

        for event_id in event_ids:
            time.sleep(REQUEST_DELAY)
            try:
                decks = get_event_deck_ids(event_id, format_code)
            except requests.RequestException as e:
                print(f"  [!] Erreur événement {event_id} : {e}")
                continue

            print(f"    événement {event_id} : {len(decks)} deck(s) trouvé(s)")

            for deck_id, archetype in decks:
                time.sleep(REQUEST_DELAY)
                try:
                    cards = get_deck_cards(event_id, deck_id, format_code)
                except requests.RequestException:
                    continue
                print(f"      deck {deck_id} ({archetype[:30]}) : {len(cards)} carte(s) trouvée(s)")
                for card_name in set(cards):  # set : une carte ne compte qu'une fois par deck
                    if card_name not in card_appearances:
                        card_appearances[card_name] = {"count": 0, "example_event_id": event_id, "example_deck_id": deck_id}
                    card_appearances[card_name]["count"] += 1

        print(f"  {len(event_ids)} événement(s) scanné(s), {len(card_appearances)} carte(s) distinctes vues.")

        for card_name, info in card_appearances.items():
            count = info["count"]
            if count < MIN_APPEARANCES:
                continue

            rarity = get_card_rarity(card_name, rarity_cache)
            if rarity not in ("rare", "mythic"):
                continue

            baseline_pct = get_baseline_pct(card_name, format_name, tournament_signals)
            if baseline_pct is not None and baseline_pct > MAX_BASELINE_PCT:
                continue  # déjà un staple connu, pas un signal précoce

            baseline_txt = f"{baseline_pct}%" if baseline_pct is not None else "hors top 50"
            summary = (
                f"Performe en {format_name} sans être largement adoptée : vue dans {count} deck(s) "
                f"classé(s) récemment, adoption globale {baseline_txt}."
            )
            event_url = f"{BASE_URL}/event?e={info['example_event_id']}&d={info['example_deck_id']}&f={format_code}"

            signal = {
                "name": card_name,
                "format": format_name,
                "rarity": rarity,
                "appearances": count,
                "baseline_pct": baseline_pct,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "url": event_url,
                "summary": summary,
                "source": "MTGTop8",
            }
            signals.append(signal)
            send_performance_alert(signal)

    existing = load_json(PERFORMANCE_SIGNALS_PATH)
    existing_list = existing if isinstance(existing, list) else []
    all_signals = (signals + existing_list)[:200]
    PERFORMANCE_SIGNALS_PATH.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2), encoding="utf-8")
    RARITY_CACHE_PATH.write_text(json.dumps(rarity_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(signals)} signal(aux) de performance sous-évaluée détecté(s).")
    return signals


if __name__ == "__main__":
    run_scan()
