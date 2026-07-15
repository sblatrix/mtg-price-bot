"""
Télécharge le guide de prix officiel Cardmarket (gratuit, public, mis à jour
une fois par jour par Cardmarket eux-mêmes - donc pas la peine de le lancer
plus d'une fois par jour) et l'enregistre pour toutes les cartes qu'on suit
et dont on connaît le cardmarket_id (via cardmarket_ids.py, alimenté
gratuitement par collector.py et post_release_scanner.py).

Fournit : Low, Avg (moyenne), Trend, Avg1/7/30 jours - en standard ET en
foil. C'est strictement plus détaillé que ce que Scryfall expose.

Limite assumée : pas de séparation par langue (FR/EN) - cette donnée n'est
disponible nulle part gratuitement, y compris dans ce fichier officiel.

Source : https://www.cardmarket.com/en/Magic/Data/Price-Guide
"""
import json

import requests

from db import init_db, insert_cardmarket_official_price
import cardmarket_ids

PRICE_GUIDE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"


def download_price_guide() -> dict:
    """Télécharge le guide de prix complet et l'indexe par idProduct."""
    resp = requests.get(PRICE_GUIDE_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    guides = data.get("priceGuides", [])
    print(f"Guide de prix téléchargé : {len(guides)} produits, généré le {data.get('createdAt')}")

    return {g["idProduct"]: g for g in guides}


def run():
    init_db()
    cm_ids = cardmarket_ids.load()

    if not cm_ids:
        print("[!] Aucun cardmarket_id connu. Lance collector.py ou post_release_scanner.py "
              "au moins une fois d'abord (ils alimentent ce cache automatiquement).")
        return

    print(f"{len(cm_ids)} carte(s) avec un cardmarket_id connu.")

    try:
        price_index = download_price_guide()
    except requests.RequestException as e:
        print(f"[!] Erreur de téléchargement du guide de prix : {e}")
        return
    except (ValueError, KeyError) as e:
        print(f"[!] Erreur de parsing du guide de prix : {e}")
        return

    matched = 0
    for card_name, cm_id in cm_ids.items():
        guide_entry = price_index.get(cm_id)
        if not guide_entry:
            continue
        insert_cardmarket_official_price(card_name, cm_id, guide_entry)
        matched += 1

    print(f"\n{matched}/{len(cm_ids)} carte(s) enrichie(s) avec les données officielles Cardmarket "
          f"(Low/Avg/Trend, standard + foil).")


if __name__ == "__main__":
    run()
