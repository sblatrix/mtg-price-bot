"""
Client pour l'API interne CardNexus (api.cardnexus.com/orpc/...).
Reverse-engineered depuis le trafic Network du navigateur - pas d'API publique
officielle documentée. À surveiller : ces endpoints peuvent changer sans préavis.

IMPORTANT : si tu obtiens des 401/403, c'est probablement que ces endpoints
nécessitent une session authentifiée (Clerk). Dans ce cas il faudra copier le
cookie de session depuis ton navigateur (onglet Application > Cookies) et le
passer ici. On avisera si ça arrive.
"""
import requests

BASE_URL = "https://api.cardnexus.com/orpc"
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://cardnexus.com",
    "Referer": "https://cardnexus.com/",
}


def _post(path: str, payload: dict) -> dict:
    """Appelle un endpoint oRPC. CardNexus enveloppe requêtes/réponses dans {"json": ...}."""
    resp = requests.post(
        f"{BASE_URL}/{path}",
        json={"json": payload},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("json", data)


def search_feed(marketplace: str = "Cardmarket", price_min: float = 0.5,
                 price_max: float = 100_000_000, limit: int = 20,
                 offset: int = 0, good_deals_only: bool = True) -> dict:
    """
    Récupère le flux des annonces (potentiellement 'bonnes affaires').
    Retourne toutes catégories de jeux confondues - on filtre côté client.
    """
    payload = {
        "limit": limit,
        "offset": offset,
        "goodDeals": good_deals_only,
        "marketplace": marketplace,
        "listing": {
            "priceRange": {"min": price_min, "max": price_max},
        },
        "sort": [["createdAt", "desc"]],
    }
    return _post("listings/searchFeed", payload)


def get_product_prices(product_id: str, finish: str, start_date: str,
                        end_date: str, marketplace: str = "Cardmarket") -> dict:
    """Récupère l'historique de prix quotidien d'un produit CardNexus."""
    payload = {
        "productId": product_id,
        "finish": finish,
        "prices": {
            "marketplace": marketplace,
            "timeRange": {"startDate": start_date, "endDate": end_date},
        },
    }
    return _post("price/getProductPrices", payload)
