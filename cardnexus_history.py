"""
Pour chaque carte de la watchlist, cherche son productId dans le catalogue
auto-construit par deal_scanner.py, puis récupère son historique de prix
CardNexus complet (30 derniers jours en un seul appel) et l'enregistre en base.

Prérequis : avoir fait tourner deal_scanner.py au moins une fois pour que
product_catalog.json contienne des entrées. Si une carte de la watchlist n'y
est pas encore, elle sera ignorée avec un message (elle finira par apparaître
au fil des scans si elle passe dans le flux, ou on ajoutera un lookup manuel
plus tard si besoin).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cardnexus_client import get_product_prices
from db import init_db, insert_price_dated

CATALOG_PATH = Path(__file__).parent / "product_catalog.json"
WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"
LOOKBACK_DAYS = 30


def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        return {}
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def find_product_id(card_name: str, catalog: dict) -> str | None:
    name_lower = card_name.strip().lower()
    for pid, info in catalog.items():
        if info.get("name", "").strip().lower() == name_lower:
            return pid
    return None


def date_range():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return start.isoformat(timespec="milliseconds").replace("+00:00", "Z"), \
           end.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run():
    init_db()
    catalog = load_catalog()
    watchlist = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    start_date, end_date = date_range()

    if not catalog:
        print("[!] Catalogue vide. Lance deal_scanner.py au moins une fois d'abord.")
        return

    for entry in watchlist["cards"]:
        name = entry["name"]
        product_id = find_product_id(name, catalog)

        if not product_id:
            print(f"[!] '{name}' pas encore vue dans le flux CardNexus, ignorée pour l'instant.")
            continue

        print(f"Récupération historique CardNexus : {name} ({product_id})...")
        for finish in ("Standard", "Foil"):
            try:
                data = get_product_prices(product_id, finish, start_date, end_date)
            except requests.RequestException as e:
                print(f"  [!] Erreur réseau ({finish}) : {e}")
                continue

            points = data.get("marketplaces", {}).get("Cardmarket", [])
            source = f"cardnexus_{finish.lower()}"
            for point in points:
                insert_price_dated(name, None, source, point.get("price"), point.get("date"))

            print(f"  -> {finish} : {len(points)} points de prix enregistrés")

    print("\nHistorique CardNexus mis à jour.")


if __name__ == "__main__":
    run()
