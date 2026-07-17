"""
Pour chaque carte suivie (watchlist manuelle + watchlist auto-générée post-
sortie), cherche son productId dans le catalogue auto-construit par
deal_scanner.py, puis récupère son historique de prix CardNexus complet
(30 derniers jours en un seul appel) et l'enregistre en base.

Prérequis : avoir fait tourner deal_scanner.py au moins une fois pour que
product_catalog.json contienne des entrées. Si une carte suivie n'y est pas
encore, elle sera ignorée avec un message (elle finira par apparaître au fil
des scans si elle passe dans le flux).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cardnexus_client import get_product_prices
from db import init_db, insert_price_dated, get_all_tracked_cards

CATALOG_PATH = Path(__file__).parent / "product_catalog.json"
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
    tracked_cards = get_all_tracked_cards()  # watchlist manuelle + auto-générée, toutes confondues
    start_date, end_date = date_range()

    if not catalog:
        print("[!] Catalogue vide. Lance deal_scanner.py au moins une fois d'abord.")
        return

    print(f"{len(tracked_cards)} carte(s) suivie(s) au total, recherche dans un catalogue de {len(catalog)} produit(s)...")
    matched = 0

    for name in tracked_cards:
        product_id = find_product_id(name, catalog)

        if not product_id:
            continue

        matched += 1
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

    print(f"\n{matched}/{len(tracked_cards)} carte(s) suivie(s) trouvée(s) dans le catalogue CardNexus et mises à jour.")


if __name__ == "__main__":
    run()
