"""
Collecte les prix depuis Scryfall (gratuit, sans clé API, reflète Cardmarket EUR).
Docs : https://scryfall.com/docs/api

Scryfall demande un throttle de ~50-100ms entre requêtes -> on respecte ça.
"""
import json
import time
from pathlib import Path

import requests

from db import init_db, insert_price

SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/named"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"
REQUEST_DELAY = 0.15  # secondes entre requêtes, recommandation Scryfall


def fetch_card_price(name: str, set_code: str | None = None):
    """Récupère le prix Cardmarket EUR (non-foil) d'une carte via Scryfall."""
    params = {"exact": name}
    if set_code:
        params["set"] = set_code

    resp = requests.get(
        SCRYFALL_SEARCH_URL,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )

    if resp.status_code == 404:
        print(f"  [!] Carte introuvable sur Scryfall : {name} ({set_code})")
        return None

    resp.raise_for_status()
    data = resp.json()

    prices = data.get("prices", {})
    eur = prices.get("eur")
    eur_foil = prices.get("eur_foil")

    return {
        "name": data.get("name"),
        "set": data.get("set"),
        "eur": float(eur) if eur else None,
        "eur_foil": float(eur_foil) if eur_foil else None,
    }


def run_collection(watchlist_path: Path):
    init_db()
    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))

    results = []
    for entry in watchlist["cards"]:
        name = entry["name"]
        set_code = entry.get("set")
        print(f"Récupération : {name}...")

        try:
            data = fetch_card_price(name, set_code)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau pour {name} : {e}")
            time.sleep(REQUEST_DELAY)
            continue

        if data:
            if data["eur"] is not None:
                insert_price(data["name"], data["set"], "scryfall_cardmarket_standard", data["eur"])
                print(f"  -> Standard : {data['eur']} EUR")
                results.append(data)
            if data.get("eur_foil") is not None:
                insert_price(data["name"], data["set"], "scryfall_cardmarket_foil", data["eur_foil"])
                print(f"  -> Foil : {data['eur_foil']} EUR")
            if data["eur"] is None and data.get("eur_foil") is None:
                print(f"  [!] Pas de prix EUR disponible pour {name}")
        else:
            print(f"  [!] Pas de données pour {name}")

        time.sleep(REQUEST_DELAY)

    return results


if __name__ == "__main__":
    watchlist_file = Path(__file__).parent / "watchlist.json"
    run_collection(watchlist_file)
    print("\nCollecte terminée.")
