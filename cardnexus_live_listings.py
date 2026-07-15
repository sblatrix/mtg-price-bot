"""
Récupère les annonces actives réelles (pas l'agrégat quotidien flou de
getProductPrices) directement depuis la page publique d'une carte CardNexus,
pour calculer un vrai "Low CN" = prix de l'annonce la moins chère active,
comme ce que l'humain voit affiché sur la page.

MÉTHODE - IMPORTANTE MISE EN GARDE :
La page carte de CardNexus est rendue côté serveur par Next.js, et les
données d'annonces sont embarquées dans le HTML sous forme de JSON dans un
format de streaming interne au framework (React Server Components), pas une
API JSON propre. On extrait ce JSON par regex plutôt que par un vrai parsing
- c'est fragile par nature (vérifié une seule fois manuellement dans le
navigateur, jamais testé en conditions réelles par ce script). À valider et
corriger au premier run réel.

Structure observée pour une annonce (extraite manuellement du HTML) :
{
  "inventoryId": "...",
  "listing": {"price": 1886, "currency": "USD"},   <- price en centimes
  "quantity": 1,
  "condition": "A",
  "language": "de",
  "finish": "Standard",
  "seller": {"id": "...", "username": "...", "country": "US", ...}
}

On ne garde que les annonces en EUR pour le calcul du Low (mélanger des
devises sans conversion live fausserait le résultat).
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from db import init_db, insert_price_dated

BASE_URL = "https://cardnexus.com/fr/explore/{game}/{expansion_slug}/card/{slug}"
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"

CATALOG_PATH = Path(__file__).parent / "product_catalog.json"

LISTING_RE = re.compile(
    r'"inventoryId"\s*:\s*"[^"]*"\s*,\s*"listing"\s*:\s*\{\s*"price"\s*:\s*(?P<price>\d+)\s*,\s*"currency"\s*:\s*"(?P<currency>[^"]*)"\s*\}'
    r'.*?"condition"\s*:\s*"(?P<condition>[^"]*)"\s*,\s*"language"\s*:\s*"(?P<language>[^"]*)"\s*,\s*"finish"\s*:\s*"(?P<finish>[^"]*)"',
    re.DOTALL,
)


def check_robots_allowed() -> bool:
    try:
        resp = requests.get("https://cardnexus.com/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
        if resp.status_code != 200:
            return True
        text = resp.text.lower()
        if "disallow: /explore" in text or "disallow: /fr/explore" in text:
            print("[!] robots.txt interdit /explore. Scan annulé.")
            return False
        return True
    except requests.RequestException:
        print("[!] Impossible de vérifier robots.txt, on continue par défaut.")
        return True


def load_catalog() -> dict:
    if CATALOG_PATH.exists():
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {}


def fetch_live_listings(expansion_slug: str, slug: str) -> list[dict]:
    url = BASE_URL.format(game="mtg", expansion_slug=expansion_slug, slug=slug)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()

    text = resp.text.replace('\\"', '"').replace('\\\\', '\\')

    listings = []
    for m in LISTING_RE.finditer(text):
        listings.append({
            "price": int(m.group("price")) / 100,
            "currency": m.group("currency"),
            "condition": m.group("condition"),
            "language": m.group("language"),
            "finish": m.group("finish"),
        })
    return listings


def today_iso_midnight() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def run():
    if not check_robots_allowed():
        return

    init_db()
    catalog = load_catalog()
    if not catalog:
        print("[!] product_catalog.json vide ou absent. Lance deal_scanner.py au moins une fois d'abord.")
        return

    today = today_iso_midnight()
    matched = 0

    for product_id, info in catalog.items():
        name = info.get("name")
        slug = info.get("slug")
        expansion_slug = info.get("expansionSlug")
        if not (name and slug and expansion_slug):
            continue

        print(f"Récupération annonces : {name}...")
        try:
            listings = fetch_live_listings(expansion_slug, slug)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            continue

        if not listings:
            print(f"  [!] Aucune annonce trouvée (parsing peut-être cassé - à vérifier)")
            continue

        for finish in ("Standard", "Foil"):
            eur_prices = [l["price"] for l in listings if l["finish"] == finish and l["currency"] == "EUR"]
            if not eur_prices:
                continue
            low = min(eur_prices)
            source = f"cardnexus_live_low_{finish.lower()}"
            insert_price_dated(name, None, source, low, today)
            print(f"  -> {finish} : Low = {low}€ ({len(eur_prices)} annonce(s) EUR)")
            matched += 1

    print(f"\n{matched} finition(s) mise(s) à jour avec un vrai Low CN.")


if __name__ == "__main__":
    run()
