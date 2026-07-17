"""
Collecte les prix depuis Scryfall (gratuit, sans clé API, reflète Cardmarket EUR).
Docs : https://scryfall.com/docs/api

Scryfall demande un throttle de ~50-100ms entre requêtes -> on respecte ça.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from db import init_db, insert_price_dated
import cardmarket_ids

SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_PRINTS_SEARCH_URL = "https://api.scryfall.com/cards/search"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"
REQUEST_DELAY = 0.15  # secondes entre requêtes, recommandation Scryfall


def today_iso_midnight() -> str:
    """Scryfall ne remet à jour son prix qu'1x/jour côté source - on force donc
    1 seul point enregistré par jour même si ce script tourne toutes les
    heures (insert_price_dated dédoublonne sur cette clé)."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _to_result(data: dict) -> dict:
    prices = data.get("prices", {})
    eur = prices.get("eur")
    eur_foil = prices.get("eur_foil")
    return {
        "name": data.get("name"),
        "set": data.get("set"),
        "eur": float(eur) if eur else None,
        "eur_foil": float(eur_foil) if eur_foil else None,
        "cardmarket_id": data.get("cardmarket_id"),
    }


def fetch_cheapest_priced_print(name: str):
    """Repli : l'impression 'par défaut' de Scryfall n'a parfois aucun prix
    (aucune annonce active sur cette impression précise), alors qu'une AUTRE
    impression de la même carte en a un (ex: Predict/Odyssey vs sa réimpression
    The List, sans prix). On cherche parmi toutes les impressions celle qui a
    un prix, en gardant la moins chère si plusieurs en ont un."""
    resp = requests.get(
        SCRYFALL_PRINTS_SEARCH_URL,
        params={"q": f'!"{name}"', "unique": "prints", "order": "eur"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None

    for card in resp.json().get("data", []):
        result = _to_result(card)
        if result["eur"] is not None or result["eur_foil"] is not None:
            return result
    return None


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
    result = _to_result(data)

    # si l'impression par défaut n'a aucun prix ET qu'on n'a pas demandé une
    # édition précise, on cherche parmi toutes les impressions de cette carte
    if result["eur"] is None and result["eur_foil"] is None and not set_code:
        fallback = fetch_cheapest_priced_print(name)
        if fallback:
            print(f"  (impression par défaut sans prix, repli sur {fallback['set']})")
            return fallback

    return result


def run_collection(watchlist_path: Path):
    init_db()
    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
    cm_ids = cardmarket_ids.load()

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
            cardmarket_ids.update(cm_ids, data["name"], data.get("cardmarket_id"))
            today = today_iso_midnight()
            if data["eur"] is not None:
                insert_price_dated(data["name"], data["set"], "scryfall_cardmarket_standard", data["eur"], today)
                print(f"  -> Standard : {data['eur']} EUR")
                results.append(data)
            if data.get("eur_foil") is not None:
                insert_price_dated(data["name"], data["set"], "scryfall_cardmarket_foil", data["eur_foil"], today)
                print(f"  -> Foil : {data['eur_foil']} EUR")
            if data["eur"] is None and data.get("eur_foil") is None:
                print(f"  [!] Pas de prix EUR disponible pour {name}")
        else:
            print(f"  [!] Pas de données pour {name}")

        time.sleep(REQUEST_DELAY)

    cardmarket_ids.save(cm_ids)
    return results


if __name__ == "__main__":
    watchlist_file = Path(__file__).parent / "watchlist.json"
    run_collection(watchlist_file)
    print("\nCollecte terminée.")
