"""
Scanne systématiquement TOUTES les rares/mythiques des sets actuellement en
fenêtre post-sortie (cf. release_calendar.py), sans dépendre du hasard des
autres flux (deal_scanner, tournament_scanner).

Pour chaque set en fenêtre :
1. Récupère la liste complète de ses rares/mythiques via Scryfall (1 requête
   paginée, gratuite)
2. Enregistre le prix Cardmarket standard + foil de chacune dans prices.db
3. Calcule une vraie rupture de tendance (breakout.py) et alerte

Limite connue : les prix ici sont ceux de Scryfall (un seul prix par carte,
pas de détail Low/Trend/Avg ni de séparation par langue). Pour ce niveau de
détail il faudrait l'API officielle Cardmarket (OAuth) ou un endpoint
CardNexus supplémentaire non encore identifié.
"""
import json
import os
import time
from pathlib import Path

import requests

from db import init_db, insert_price
from release_calendar import load_release_cache, is_in_post_release_window, refresh_set_cache
from breakout import compute_breakout

SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"
REQUEST_DELAY = 0.1

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
AUTO_WATCHLIST_PATH = Path(__file__).parent / "post_release_watchlist.json"

BREAKOUT_THRESHOLD_PCT = 15.0


def get_post_release_set_codes(release_cache: dict) -> list[str]:
    return [code for code in release_cache if is_in_post_release_window(code, release_cache)]


def fetch_rares_mythics(set_code: str) -> list[dict]:
    """Récupère toutes les rares/mythiques d'un set via l'API de recherche Scryfall (paginée)."""
    cards = []
    url = SCRYFALL_SEARCH_URL
    params = {"q": f"set:{set_code} (rarity:rare or rarity:mythic)", "order": "name"}

    while url:
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=20)
        if resp.status_code == 404:
            break  # set sans rares/mythiques trouvées (ou code invalide)
        resp.raise_for_status()
        data = resp.json()

        for card in data.get("data", []):
            prices = card.get("prices", {})
            cards.append({
                "name": card.get("name"),
                "set": card.get("set"),
                "rarity": card.get("rarity"),
                "eur": float(prices["eur"]) if prices.get("eur") else None,
                "eur_foil": float(prices["eur_foil"]) if prices.get("eur_foil") else None,
            })

        url = data.get("next_page")
        params = {}  # next_page contient déjà tous les paramètres
        time.sleep(REQUEST_DELAY)

    return cards


def send_breakout_alert(breakout: dict, set_code: str):
    direction = "hausse" if breakout["change_pct"] > 0 else "baisse"
    emoji = "🚨📈" if breakout["change_pct"] > 0 else "📉"
    finish = "Foil" if breakout["source"].endswith("_foil") else "Standard"
    message = {
        "content": (
            f"{emoji} **{breakout['card_name']}** ({finish}, {set_code}) — rupture de {direction} "
            f"détectée : **{breakout['change_pct']:+.1f}%**\n"
            f"Moyenne récente ({breakout['n_recent']}j) : {breakout['recent_avg']}€ | "
            f"Référence ({breakout['n_baseline']}j) : {breakout['baseline_avg']}€"
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


def run():
    init_db()

    print("Rafraîchissement du calendrier des sorties...")
    try:
        release_cache = refresh_set_cache()
    except requests.RequestException:
        release_cache = load_release_cache()

    set_codes = get_post_release_set_codes(release_cache)
    print(f"{len(set_codes)} set(s) en fenêtre post-sortie : {', '.join(set_codes) if set_codes else '(aucun)'}")

    auto_watchlist = set()
    if AUTO_WATCHLIST_PATH.exists():
        auto_watchlist = set(json.loads(AUTO_WATCHLIST_PATH.read_text()))

    breakouts_found = 0

    for set_code in set_codes:
        print(f"\nScan de {set_code}...")
        try:
            cards = fetch_rares_mythics(set_code)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            continue

        print(f"  {len(cards)} rare(s)/mythique(s) trouvée(s)")

        for card in cards:
            auto_watchlist.add(card["name"])

            if card["eur"] is not None:
                insert_price(card["name"], card["set"], "scryfall_cardmarket_standard", card["eur"])
            if card["eur_foil"] is not None:
                insert_price(card["name"], card["set"], "scryfall_cardmarket_foil", card["eur_foil"])

            for source in ("scryfall_cardmarket_standard", "scryfall_cardmarket_foil"):
                breakout = compute_breakout(card["name"], source)
                if breakout and abs(breakout["change_pct"]) >= BREAKOUT_THRESHOLD_PCT:
                    send_breakout_alert(breakout, set_code)
                    breakouts_found += 1

    AUTO_WATCHLIST_PATH.write_text(json.dumps(sorted(auto_watchlist), ensure_ascii=False, indent=2))
    print(f"\n{len(auto_watchlist)} carte(s) au total dans la watchlist auto-générée. "
          f"{breakouts_found} rupture(s) de tendance détectée(s).")


if __name__ == "__main__":
    run()
