"""
Scanne les pages "format staples" de MTGGoldfish (classement des cartes les
plus jouées par format, % de decks + copies moyennes) et détecte :
- les nouvelles entrées (carte jamais vue dans le classement avant)
- les fortes progressions de % de decks d'un scan à l'autre

Filtre sur rare/mythique uniquement (les commons/uncommons ont trop de
supply pour que ça vaille le coup, cf. discussion projet). Croise avec
release_calendar.py pour prioriser les cartes en fenêtre post-sortie.

Fragilité connue : ceci scrape du HTML public, pas une API officielle.
Si MTGGoldfish change la structure de sa page, le parsing peut casser -
c'est attendu, à surveiller.
"""
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone

import os

import requests
from bs4 import BeautifulSoup

from release_calendar import load_release_cache, refresh_set_cache, is_in_post_release_window

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

BASE_URL = "https://www.mtggoldfish.com/format-staples/{format}/full/all"
FORMATS = ["standard", "modern", "pioneer", "legacy", "commander"]
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"

HISTORY_PATH = Path(__file__).parent / "tournament_signals.json"
RARITY_CACHE_PATH = Path(__file__).parent / "rarity_cache.json"

# Seuils de détection
MIN_PCT_INCREASE = 5.0   # points de % gagnés d'un scan à l'autre pour signaler
MIN_PCT_NEW_ENTRY = 8.0  # % minimum pour signaler une carte totalement nouvelle au classement

CARD_ID_RE = re.compile(r"^(.*?)\s*\[([A-Za-z0-9]+)\]$")


def fetch_staples(format_name: str) -> list[dict]:
    """Récupère et parse le classement des cartes les plus jouées pour un format.

    Structure réelle de la page (vérifiée sur le HTML brut) :
    <tr>
      <td class='text-end'>RANG</td>
      <td class='col-card'><a data-card-id="Nom [SET]" href="...">Nom</a></td>
      <td>...cout (icônes, pas de texte utile)...</td>
      <td class='text-end'>XX%</td>
      <td class='text-end'>Y.Y</td>
    </tr>
    """
    url = BASE_URL.format(format=format_name)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = []

    for row in soup.find_all("tr"):
        link = row.find("a", attrs={"data-card-id": True})
        if not link:
            continue

        card_id = link["data-card-id"]
        match = CARD_ID_RE.match(card_id)
        if match:
            name, set_code = match.group(1).strip(), match.group(2).lower()
        else:
            name, set_code = card_id.strip(), None

        href = link.get("href", "")
        card_url = f"https://www.mtggoldfish.com{href}" if href.startswith("/") else href

        cells = row.find_all("td")
        pct = None
        copies = None
        for i, cell in enumerate(cells):
            text = cell.get_text(strip=True)
            pct_match = re.match(r"^(\d+)%$", text)
            if pct_match:
                pct = int(pct_match.group(1))
                if i + 1 < len(cells):
                    try:
                        copies = float(cells[i + 1].get_text(strip=True))
                    except ValueError:
                        copies = None
                break

        if name and pct is not None:
            entries.append({
                "name": name,
                "set_code": set_code,
                "pct_of_decks": pct,
                "avg_copies": copies,
                "url": card_url,
            })

    return entries


def load_rarity_cache() -> dict:
    if RARITY_CACHE_PATH.exists():
        return json.loads(RARITY_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def get_card_rarity(name: str, set_code: str | None, cache: dict) -> str | None:
    """Rareté via Scryfall, mise en cache localement pour ne pas re-requêter à chaque scan.
    Utilise le set_code fourni par MTGGoldfish pour cibler la bonne impression exacte."""
    cache_key = f"{name}|{set_code or ''}"
    if cache_key in cache:
        return cache[cache_key].get("rarity")

    try:
        params = {"exact": name}
        if set_code:
            params["set"] = set_code
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            cache[cache_key] = {"rarity": None, "set": set_code}
            return None
        data = resp.json()
        rarity = data.get("rarity")
        cache[cache_key] = {"rarity": rarity, "set": set_code}
        return rarity
    except requests.RequestException:
        return None
    finally:
        time.sleep(0.1)


def load_history() -> dict:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    return {}


def send_signal_alert(signal: dict):
    tag = "🆕 Nouvelle entrée" if signal["is_new_entry"] else f"📈 +{signal['pct_change']}pts"
    window_tag = "\n⏱️ **En fenêtre post-sortie** — priorité haute" if signal["in_post_release_window"] else ""
    message = {
        "content": (
            f"🎯 **{signal['name']}** ({signal['rarity']}) perce en **{signal['format']}**\n"
            f"{tag} — {signal['pct_of_decks']}% des decks du classement"
            f"{window_tag}"
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
    history = load_history()
    rarity_cache = load_rarity_cache()

    print("Rafraîchissement du calendrier des sorties (Scryfall)...")
    try:
        release_cache = refresh_set_cache()
    except requests.RequestException as e:
        print(f"  [!] Échec du rafraîchissement, utilisation du cache existant : {e}")
        release_cache = load_release_cache()

    now = datetime.now(timezone.utc).isoformat()

    signals = []

    for fmt in FORMATS:
        print(f"Scan MTGGoldfish : {fmt}...")
        try:
            entries = fetch_staples(fmt)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            continue

        prev_entries = {e["name"]: e for e in history.get(fmt, {}).get("entries", [])}

        for entry in entries:
            name = entry["name"]
            set_code = entry.get("set_code")
            rarity = get_card_rarity(name, set_code, rarity_cache)
            if rarity not in ("rare", "mythic"):
                continue  # trop de supply sinon, cf. discussion projet

            in_window = is_in_post_release_window(set_code, release_cache) if set_code else False

            prev = prev_entries.get(name)
            is_new = prev is None
            pct_change = entry["pct_of_decks"] - prev["pct_of_decks"] if prev else entry["pct_of_decks"]

            triggered = (is_new and entry["pct_of_decks"] >= MIN_PCT_NEW_ENTRY) or \
                        (not is_new and pct_change >= MIN_PCT_INCREASE)

            if triggered:
                if is_new:
                    summary = f"Nouvelle entrée en {fmt} : {entry['pct_of_decks']}% des decks du classement."
                else:
                    summary = f"Progression en {fmt} : +{pct_change:.0f} points, désormais {entry['pct_of_decks']}% des decks."

                signal = {
                    "name": name,
                    "format": fmt,
                    "rarity": rarity,
                    "pct_of_decks": entry["pct_of_decks"],
                    "pct_change": pct_change,
                    "is_new_entry": is_new,
                    "in_post_release_window": in_window,
                    "detected_at": now,
                    "url": entry.get("url"),
                    "summary": summary,
                    "source": "MTGGoldfish",
                }
                signals.append(signal)
                send_signal_alert(signal)

        history[fmt] = {"updated_at": now, "entries": entries}

    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    RARITY_CACHE_PATH.write_text(json.dumps(rarity_cache, ensure_ascii=False, indent=2), encoding="utf-8")

    signals_path = Path(__file__).parent / "meta_signals.json"
    existing_signals = []
    if signals_path.exists():
        existing_signals = json.loads(signals_path.read_text(encoding="utf-8"))
    all_signals = (signals + existing_signals)[:200]
    signals_path.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(signals)} nouveau(x) signal(aux) rare/mythique détecté(s).")
    return signals


if __name__ == "__main__":
    run_scan()
