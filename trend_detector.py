"""
Analyse l'historique de prix et détecte les variations significatives.
Envoie une alerte Discord (webhook gratuit) si un seuil est franchi.

Config nécessaire (variable d'environnement) :
    DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

Comment créer un webhook Discord gratuit :
    Paramètres du salon -> Intégrations -> Webhooks -> Nouveau webhook -> Copier l'URL
"""
import os
from datetime import datetime, timezone

import requests

from db import get_all_tracked_cards, get_latest_price, get_price_history

# Seuil d'écart entre sources pour alerter (en %)
CROSS_SOURCE_THRESHOLD_PERCENT = 15.0

# Seuil de variation pour déclencher une alerte (en %)
THRESHOLD_PERCENT = 10.0
# Sur combien des dernières collectes on compare (ex: 5 derniers relevés)
LOOKBACK = 5

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


def compute_trend(card_name: str, source: str = "scryfall_cardmarket"):
    history = get_price_history(card_name, source, limit=LOOKBACK)
    if len(history) < 2:
        return None

    # history est trié du plus récent au plus ancien
    latest = history[0]["price_eur"]
    oldest = history[-1]["price_eur"]

    if not oldest or oldest == 0:
        return None

    change_pct = ((latest - oldest) / oldest) * 100
    return {
        "card_name": card_name,
        "latest_price": latest,
        "oldest_price": oldest,
        "change_pct": change_pct,
        "num_points": len(history),
    }


def send_discord_alert(trend: dict):
    if not DISCORD_WEBHOOK_URL:
        print(f"  [!] DISCORD_WEBHOOK_URL non configuré, alerte affichée en console uniquement")
        print(f"      {trend['card_name']} : {trend['change_pct']:+.1f}% -> {trend['latest_price']}€")
        return

    direction = "hausse" if trend["change_pct"] > 0 else "baisse"
    emoji = "📈" if trend["change_pct"] > 0 else "📉"

    payload = {
        "content": (
            f"{emoji} **{trend['card_name']}** en {direction} de "
            f"**{trend['change_pct']:+.1f}%**\n"
            f"Prix actuel : {trend['latest_price']}€ "
            f"(était {trend['oldest_price']}€ sur les {trend['num_points']} derniers relevés)"
        )
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"  -> Alerte Discord envoyée pour {trend['card_name']}")
    except requests.RequestException as e:
        print(f"  [!] Échec envoi Discord pour {trend['card_name']} : {e}")


def compute_cross_source_gap(card_name: str, finish: str = "standard"):
    """Compare le dernier prix connu Scryfall/Cardmarket vs CardNexus, même finition."""
    scryfall_row = get_latest_price(card_name, f"scryfall_cardmarket_{finish}")
    cardnexus_row = get_latest_price(card_name, f"cardnexus_{finish}")

    if not scryfall_row or not cardnexus_row:
        return None

    p1, p2 = scryfall_row["price_eur"], cardnexus_row["price_eur"]
    if not p1 or not p2 or min(p1, p2) == 0:
        return None

    gap_pct = (abs(p1 - p2) / min(p1, p2)) * 100
    cheaper_source = "cardnexus" if p2 < p1 else "scryfall_cardmarket"

    return {
        "card_name": card_name,
        "finish": finish,
        "scryfall_price": p1,
        "cardnexus_price": p2,
        "gap_pct": gap_pct,
        "cheaper_source": cheaper_source,
    }


def send_cross_source_alert(gap: dict):
    message = (
        f"⚖️ **{gap['card_name']}** ({gap['finish']}) — écart de **{gap['gap_pct']:.0f}%** entre sources\n"
        f"Cardmarket (via Scryfall) : {gap['scryfall_price']}€ | "
        f"CardNexus : {gap['cardnexus_price']}€\n"
        f"Moins cher sur : **{gap['cheaper_source']}**"
    )
    if not DISCORD_WEBHOOK_URL:
        print(f"  [!] {message}")
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10)
        resp.raise_for_status()
        print(f"  -> Alerte écart envoyée : {gap['card_name']}")
    except requests.RequestException as e:
        print(f"  [!] Échec envoi Discord : {e}")


def run_detection():
    cards = get_all_tracked_cards()
    print(f"Analyse de {len(cards)} carte(s) suivie(s)...\n")

    alerts_sent = 0
    for card_name in cards:
        for finish in ("standard", "foil"):
            source = f"scryfall_cardmarket_{finish}"
            trend = compute_trend(card_name, source=source)
            if trend is not None:
                if abs(trend["change_pct"]) >= THRESHOLD_PERCENT:
                    trend["card_name"] = f"{card_name} ({finish})"
                    send_discord_alert(trend)
                    alerts_sent += 1
                else:
                    print(f"  {card_name} [{finish}] : {trend['change_pct']:+.1f}% (sous le seuil)")

            gap = compute_cross_source_gap(card_name, finish=finish)
            if gap and gap["gap_pct"] >= CROSS_SOURCE_THRESHOLD_PERCENT:
                send_cross_source_alert(gap)
                alerts_sent += 1

    print(f"\n{alerts_sent} alerte(s) déclenchée(s) sur {len(cards)} cartes analysées.")


if __name__ == "__main__":
    run_detection()
