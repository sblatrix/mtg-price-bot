"""
Cache local nom de carte -> cardmarket_id (idProduct Cardmarket).
Alimenté gratuitement à chaque appel Scryfall existant (le champ
cardmarket_id est déjà présent dans toute réponse Scryfall), pas besoin de
requêtes dédiées.
"""
import json
from pathlib import Path

CACHE_PATH = Path(__file__).parent / "cardmarket_ids.json"


def load() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def update(cache: dict, card_name: str, cardmarket_id):
    if cardmarket_id:
        cache[card_name] = cardmarket_id
