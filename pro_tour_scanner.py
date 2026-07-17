"""
Scanner spécifique au Pro Tour Marvel Super Heroes (juillet 2026) - scrape
les decklists officielles publiées par Wizards sur magic.gg.

IMPORTANT : les URLs ci-dessous sont propres à CET événement précis. Pour un
futur Pro Tour, il faudra les mettre à jour (pas trouvé de page d'index
générique qui liste automatiquement les decklists de l'événement en cours -
magic.gg/decklists semble être une page qui nécessite du JS pour lister les
événements récents).

Format de page très simple à parser : une ligne "N Nom de carte" par carte,
deck après deck, sans séparateur explicite entre les decks dans le texte -
donc on ne peut pas isoler un deck individuel, mais on peut compter le nombre
total de mentions de chaque carte sur l'ensemble des decklists Modern de
l'événement, ce qui est déjà un signal fort (Pro Tour = les meilleurs decks
du monde, sur un format donné, à un instant T).
"""
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

# URLs spécifiques au Pro Tour Marvel Super Heroes, Modern - à mettre à jour
# pour le prochain événement
DECKLIST_URLS = {
    "A-E": "https://magic.gg/decklists/pro-tour-marvel-super-heroes-modern-decklists-a-e",
    "F-K": "https://magic.gg/decklists/pro-tour-marvel-super-heroes-modern-decklists-f-k",
    "L-P": "https://magic.gg/decklists/pro-tour-marvel-super-heroes-modern-decklists-l-p",
    "R-Z": "https://magic.gg/decklists/pro-tour-marvel-super-heroes-modern-decklists-r-z",
}
EVENT_LABEL = "Pro Tour Marvel Super Heroes (Modern)"
EVENT_URL = "https://magic.gg/news/pro-tour-marvel-super-heroes-modern-decklists"
USER_AGENT = "Mozilla/5.0 (compatible; personal-price-tracker/1.0)"
REQUEST_DELAY = 1.0

CARD_LINE_RE = re.compile(r"^(\d+)\s+(.+)$")
MIN_MENTIONS = 3  # nombre de lignes minimum pour retenir une carte comme signal

RARITY_CACHE_PATH = Path(__file__).parent / "rarity_cache.json"
OUTPUT_PATH = Path(__file__).parent / "pro_tour_signals.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def fetch_card_mentions(url: str) -> Counter:
    """Récupère toutes les lignes 'N Nom de carte' d'une page de decklists.
    Le texte de nav/footer ne matche pas ce format (pas de chiffre en tête),
    donc pas besoin d'isoler la zone de contenu."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()

    counter = Counter()
    for line in resp.text.split("\n"):
        line = line.strip()
        m = CARD_LINE_RE.match(line)
        if m:
            count, name = int(m.group(1)), m.group(2).strip()
            if 0 < count <= 4 and len(name) > 2 and not name.isdigit():
                counter[name] += 1
    return counter


def load_rarity_cache() -> dict:
    if RARITY_CACHE_PATH.exists():
        return json.loads(RARITY_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def get_card_rarity(name: str, cache: dict) -> str | None:
    cache_key = next((k for k in cache if k.startswith(f"{name}|")), None)
    if cache_key:
        return cache[cache_key].get("rarity")

    try:
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": name},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        rarity = resp.json().get("rarity")
        cache[f"{name}|"] = {"rarity": rarity, "set": None}
        return rarity
    except requests.RequestException:
        return None
    finally:
        time.sleep(0.1)


def send_pro_tour_alert(name: str, rarity: str, mentions: int):
    message = {
        "content": (
            f"🏆 **{name}** ({rarity}) — vue dans {mentions} decklist(s) Modern au {EVENT_LABEL}\n"
            f"{EVENT_URL}"
        )
    }
    if not DISCORD_WEBHOOK_URL:
        print(f"  [!] {message['content']}")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=message, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Échec envoi Discord : {e}")


def run():
    total_counter = Counter()

    for label, url in DECKLIST_URLS.items():
        print(f"Scan {label}...")
        try:
            counter = fetch_card_mentions(url)
        except requests.RequestException as e:
            print(f"  [!] Erreur réseau : {e}")
            time.sleep(REQUEST_DELAY)
            continue
        print(f"  {sum(counter.values())} ligne(s) de carte trouvée(s), {len(counter)} carte(s) distincte(s).")
        total_counter.update(counter)
        time.sleep(REQUEST_DELAY)

    rarity_cache = load_rarity_cache()
    signals = []

    for name, mentions in total_counter.most_common():
        if mentions < MIN_MENTIONS:
            continue
        rarity = get_card_rarity(name, rarity_cache)
        if rarity not in ("rare", "mythic"):
            continue

        signal = {
            "name": name,
            "mentions": mentions,
            "rarity": rarity,
            "format": "modern",
            "event": EVENT_LABEL,
            "url": EVENT_URL,
            "summary": f"Vue dans {mentions} decklist(s) Modern au {EVENT_LABEL} — un signal de poids, "
                       f"c'est le plus haut niveau compétitif.",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "source": "Pro Tour (magic.gg)",
        }
        signals.append(signal)
        print(f"  -> SIGNAL {name} ({rarity}) : {mentions} mentions")
        send_pro_tour_alert(name, rarity, mentions)

    RARITY_CACHE_PATH.write_text(json.dumps(rarity_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_PATH.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(signals)} rare(s)/mythique(s) détectée(s) sur l'ensemble des decklists Modern du Pro Tour.")
    return signals


if __name__ == "__main__":
    run()
