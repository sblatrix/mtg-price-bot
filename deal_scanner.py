"""
Scanne le flux "bonnes affaires" de CardNexus, filtre sur MTG, et alerte
sur Discord pour les écarts les plus intéressants.

Logique de scoring :
- CardNexus calcule déjà isGoodDeal / dealMarket pour chaque annonce.
- On calcule en plus notre propre % d'écart entre le prix demandé et le prix
  de référence (priceEu pour du EUR, priceUs sinon) pour prioriser.
- On garde un historique des IDs déjà alertés pour ne pas spammer deux fois
  la même annonce (stocké dans seen_deals.json, simple, pas besoin de SQL ici).
"""
import json
import os
from pathlib import Path

import requests

from cardnexus_client import search_feed

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SEEN_DEALS_PATH = Path(__file__).parent / "seen_deals.json"
CATALOG_PATH = Path(__file__).parent / "product_catalog.json"
RECENT_DEALS_PATH = Path(__file__).parent / "recent_deals.json"
MAX_RECENT_DEALS = 100

# Seuil : on n'alerte que si le prix demandé est au moins X% sous le prix de référence
MIN_DISCOUNT_PERCENT = 20.0
# Combien de pages on scanne par run (20 annonces/page)
MAX_PAGES = 5


def load_seen_deals() -> set:
    if SEEN_DEALS_PATH.exists():
        return set(json.loads(SEEN_DEALS_PATH.read_text(encoding="utf-8")))
    return set()


def save_seen_deals(seen: set):
    # on garde large mais borné pour éviter que le fichier grossisse indéfiniment
    trimmed = list(seen)[-5000:]
    SEEN_DEALS_PATH.write_text(json.dumps(trimmed), encoding="utf-8")


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {}


def update_catalog(catalog: dict, product: dict):
    """Enregistre productId <-> nom/slug pour chaque carte MTG croisée dans le flux.
    Construit gratuitement un mini-catalogue au fil des runs, sans endpoint de recherche."""
    pid = product.get("id")
    if not pid:
        return
    catalog[pid] = {
        "name": product.get("name"),
        "slug": product.get("slug"),
        "nameSlug": product.get("nameSlug"),
        "expansion": product.get("expansion", {}).get("name"),
        "expansionSlug": product.get("expansion", {}).get("slug"),
        "finishes": product.get("finishes", []),
    }


def compute_discount(listing: dict) -> tuple[float, bool] | None:
    """% d'écart entre le prix payé et le prix de référence du marché.

    Retourne (discount_pct, confident). confident=False pour le foil tant
    qu'on n'a pas confirmé que priceEu/priceUs couvre bien le foil (risque
    de faux positif sinon) - dans ce cas on log mais on n'auto-alerte pas.
    """
    product = listing.get("product", {})
    ref_price = product.get("priceEu") or product.get("priceUs")
    paid_price = listing.get("priceNormalizedEur") or listing.get("priceNormalizedUsd")

    if not ref_price or not paid_price or ref_price == 0:
        return None

    paid_price_units = paid_price / 100
    discount = ((ref_price - paid_price_units) / ref_price) * 100
    confident = listing.get("finish") == "Standard"
    return discount, confident


def record_deal_for_dashboard(listing: dict, discount_pct: float, confident: bool):
    """Garde les N dernières bonnes affaires détaillées pour que le dashboard puisse les afficher."""
    product = listing["product"]
    entry = {
        "name": product.get("name"),
        "expansion": product.get("expansion", {}).get("name"),
        "finish": listing.get("finish"),
        "language": listing.get("language"),
        "price_paid": round((listing.get("priceNormalizedEur") or listing.get("priceNormalizedUsd", 0)) / 100, 2),
        "ref_price": product.get("priceEu") or product.get("priceUs"),
        "discount_pct": round(discount_pct, 1),
        "confident": confident,
        "seller_country": listing.get("sellerCountry"),
        "created_at": listing.get("createdAt"),
        "url": (
            f"https://cardnexus.com/fr/explore/{product.get('gameSlug', 'mtg')}/"
            f"{product.get('expansion', {}).get('slug', '')}/card/{product.get('slug', '')}"
            if product.get("expansion", {}).get("slug") and product.get("slug") else None
        ),
    }

    deals = []
    if RECENT_DEALS_PATH.exists():
        deals = json.loads(RECENT_DEALS_PATH.read_text(encoding="utf-8"))
    deals.insert(0, entry)
    deals = deals[:MAX_RECENT_DEALS]
    RECENT_DEALS_PATH.write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")


def send_discord_deal_alert(listing: dict, discount_pct: float, confident: bool):
    product = listing["product"]
    name = product.get("name", "Carte inconnue")
    expansion = product.get("expansion", {}).get("name", "")
    price_paid = (listing.get("priceNormalizedEur") or listing.get("priceNormalizedUsd", 0)) / 100
    ref_price = product.get("priceEu") or product.get("priceUs")
    finish = listing.get("finish", "")
    language = listing.get("language", "?")
    seller_country = listing.get("sellerCountry", "?")
    slug = product.get("slug", "")
    game = product.get("gameSlug", "mtg")
    expansion_slug = product.get("expansion", {}).get("slug", "")
    url = f"https://cardnexus.com/fr/explore/{game}/{expansion_slug}/card/{slug}" if expansion_slug and slug else ""
    tag = "💰" if confident else "⚠️ à vérifier manuellement (foil, réf. prix incertaine)"

    message = {
        "content": (
            f"{tag} **{name}** ({expansion}) — {finish}, langue {language}\n"
            f"Prix demandé : **{price_paid:.2f}€** vs référence marché **{ref_price:.2f}€** "
            f"(**-{discount_pct:.0f}%**)\n"
            f"Vendeur : {seller_country}"
            + (f"\n{url}" if url else "")
        )
    }

    if not DISCORD_WEBHOOK_URL:
        print(f"  [!] DISCORD_WEBHOOK_URL non configuré : {message['content']}")
        return

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=message, timeout=10)
        resp.raise_for_status()
        print(f"  -> Alerte envoyée : {name}")
    except requests.RequestException as e:
        print(f"  [!] Échec envoi Discord : {e}")


def run_scan():
    seen = load_seen_deals()
    new_seen = set(seen)
    catalog = load_catalog()
    alerts_sent = 0
    mtg_deals_found = 0

    for page in range(MAX_PAGES):
        offset = page * 20
        print(f"Page {page + 1}/{MAX_PAGES} (offset {offset})...")

        try:
            data = search_feed(offset=offset, limit=20, good_deals_only=True)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            break

        listings = data.get("listings", [])
        if not listings:
            print("  Plus de résultats, arrêt.")
            break

        for listing in listings:
            product = listing.get("product", {})
            if product.get("gameSlug") != "mtg":
                continue

            mtg_deals_found += 1
            update_catalog(catalog, product)
            listing_id = listing.get("id")
            if listing_id in seen:
                continue

            discount_result = compute_discount(listing)
            if discount_result is not None:
                discount, confident = discount_result
                if discount >= MIN_DISCOUNT_PERCENT:
                    record_deal_for_dashboard(listing, discount, confident)
                    if confident:
                        send_discord_deal_alert(listing, discount, confident=True)
                        alerts_sent += 1
                    else:
                        print(f"  [foil, à vérifier] {product.get('name')} : -{discount:.0f}% "
                              f"({listing.get('language')})")

            new_seen.add(listing_id)

        if not data.get("pagination", {}).get("hasNextPage"):
            break

    save_seen_deals(new_seen)
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{mtg_deals_found} annonce(s) MTG scannée(s), {alerts_sent} alerte(s) envoyée(s), "
          f"{len(catalog)} produit(s) MTG au catalogue.")


if __name__ == "__main__":
    run_scan()
