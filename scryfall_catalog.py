"""
Récupère et met en cache localement la liste COMPLÈTE de tous les noms de
cartes Magic existants (pas juste celles qu'on suit déjà), via le catalogue
public Scryfall. Utilisé pour repérer de quelle carte parle un article/post,
même si ce n'est pas encore une carte qu'on suit - c'est justement ce qui
permet de découvrir de nouvelles cartes à ajouter à la watchlist.

Le fichier est mis en cache (scryfall_card_names.json) et rafraîchi seulement
si absent ou vieux de plus de 7 jours - la liste des noms de cartes ne change
qu'à chaque sortie de set, pas la peine de la retélécharger à chaque run.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

CATALOG_URL = "https://api.scryfall.com/catalog/card-names"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"
CACHE_PATH = Path(__file__).parent / "scryfall_card_names.json"
CACHE_MAX_AGE_DAYS = 7


def _is_cache_fresh() -> bool:
    if not CACHE_PATH.exists():
        return False
    age = datetime.now(timezone.utc).timestamp() - CACHE_PATH.stat().st_mtime
    return age < CACHE_MAX_AGE_DAYS * 86400


def get_all_card_names(min_length: int = 6) -> set[str]:
    """Retourne l'ensemble de tous les noms de cartes Magic connus (filtré sur
    une longueur minimum pour éviter les faux positifs sur des noms trop
    courts/génériques lors du matching dans du texte libre)."""
    if _is_cache_fresh():
        names = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(CATALOG_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=30)
            resp.raise_for_status()
            names = resp.json().get("data", [])
            CACHE_PATH.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        except requests.RequestException:
            # échec réseau : retombe sur le cache existant si présent, sinon liste vide
            names = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else []

    return {n for n in names if len(n) >= min_length}
