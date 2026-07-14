"""
Détermine si une carte est dans sa "fenêtre post-sortie" (les semaines qui
suivent la sortie de son set), période où on a vu empiriquement (The Ten
Rings) que les hausses de prix pilotées par le vrai gameplay ont lieu.

Utilise l'API Scryfall /sets (gratuite, pas de clé) plutôt qu'un calendrier
tenu à la main : les dates y sont fiables et à jour, contrairement à des
articles qui parlent de sorties "à l'automne" sans date précise.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).parent / "set_release_cache.json"
SCRYFALL_SETS_URL = "https://api.scryfall.com/sets"
USER_AGENT = "MTGPriceTrendBot/1.0 (personal project)"

# Fenêtre de vigilance renforcée après une sortie (en jours)
POST_RELEASE_WINDOW_DAYS = 35


def load_release_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_set_cache() -> dict:
    """Récupère tous les sets connus de Scryfall et cache code -> date de sortie.
    À appeler périodiquement (une fois par run suffit, Scryfall met à jour en continu)."""
    resp = requests.get(SCRYFALL_SETS_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    cache = {}
    for s in data.get("data", []):
        code = s.get("code")
        released_at = s.get("released_at")
        if code and released_at:
            cache[code] = {"name": s.get("name"), "released_at": released_at}

    _save_cache(cache)
    return cache


def days_since_release(set_code: str, cache: dict | None = None) -> int | None:
    cache = cache if cache is not None else load_release_cache()
    entry = cache.get(set_code.lower())
    if not entry:
        return None
    released = datetime.fromisoformat(entry["released_at"]).replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - released
    return delta.days


def is_in_post_release_window(set_code: str, cache: dict | None = None,
                                window_days: int = POST_RELEASE_WINDOW_DAYS) -> bool:
    days = days_since_release(set_code, cache)
    if days is None:
        return False
    return 0 <= days <= window_days


if __name__ == "__main__":
    print("Rafraîchissement du cache des dates de sortie de sets...")
    cache = refresh_set_cache()
    print(f"{len(cache)} sets en cache.")

    recent = [(code, e) for code, e in cache.items() if is_in_post_release_window(code, cache)]
    recent.sort(key=lambda x: x[1]["released_at"], reverse=True)
    print(f"\n{len(recent)} set(s) actuellement en fenêtre post-sortie ({POST_RELEASE_WINDOW_DAYS}j) :")
    for code, e in recent:
        print(f"  {code} - {e['name']} ({e['released_at']})")
