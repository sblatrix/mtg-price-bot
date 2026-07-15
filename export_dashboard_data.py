"""
Compile prices.db + product_catalog.json + recent_deals.json en un seul
fichier docs/data.json, lu par le dashboard statique (docs/index.html).

Pourquoi un export statique plutôt qu'un serveur : GitHub Pages ne sert que
des fichiers statiques (pas de Python/SQLite en live), donc on regénère ce
JSON à chaque run du bot et on le commit avec le reste des données.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from db import get_connection, get_all_tracked_cards, get_latest_cardmarket_official_price
from trend_detector import compute_trend, compute_cross_source_gap

ROOT = Path(__file__).parent
DEALS_PATH = ROOT / "recent_deals.json"
OUTPUT_PATH = ROOT / "docs" / "data.json"


def get_history_series(card_name: str, source: str, limit: int = 60):
    conn = get_connection()
    rows = conn.execute(
        "SELECT price_eur, fetched_at FROM price_history "
        "WHERE card_name = ? AND source = ? ORDER BY fetched_at ASC LIMIT ?",
        (card_name, source, limit),
    ).fetchall()
    conn.close()
    return [{"date": r["fetched_at"], "price": r["price_eur"]} for r in rows]


def build_cardmarket_official_block(card_name: str):
    row = get_latest_cardmarket_official_price(card_name)
    if not row:
        return None
    return {
        "low": row["low"], "avg": row["avg"], "trend": row["trend"],
        "avg1": row["avg1"], "avg7": row["avg7"], "avg30": row["avg30"],
        "low_foil": row["low_foil"], "avg_foil": row["avg_foil"], "trend_foil": row["trend_foil"],
        "avg1_foil": row["avg1_foil"], "avg7_foil": row["avg7_foil"], "avg30_foil": row["avg30_foil"],
    }


def build_card_entry(card_name: str) -> dict:
    entry = {"name": card_name, "finishes": {}, "cardmarket_official": build_cardmarket_official_block(card_name)}

    for finish in ("standard", "foil"):
        cardnexus_history = get_history_series(card_name, f"cardnexus_{finish}")
        scryfall_history = get_history_series(card_name, f"scryfall_cardmarket_{finish}")
        trend = compute_trend(card_name, source=f"scryfall_cardmarket_{finish}")
        gap = compute_cross_source_gap(card_name, finish=finish)

        if not cardnexus_history and not scryfall_history:
            continue

        entry["finishes"][finish] = {
            "cardnexus_history": cardnexus_history,
            "scryfall_history": scryfall_history,
            "trend_pct": round(trend["change_pct"], 1) if trend else None,
            "cross_source_gap_pct": round(gap["gap_pct"], 1) if gap else None,
            "cheaper_source": gap["cheaper_source"] if gap else None,
        }

    return entry


def run():
    cards = get_all_tracked_cards()
    card_entries = [build_card_entry(name) for name in cards]
    # ne garde que les cartes avec au moins une finition ayant des données
    card_entries = [c for c in card_entries if c["finishes"]]

    recent_deals = []
    if DEALS_PATH.exists():
        recent_deals = json.loads(DEALS_PATH.read_text(encoding="utf-8"))

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": card_entries,
        "recent_deals": recent_deals,
    }

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Dashboard data exporté : {len(card_entries)} carte(s), {len(recent_deals)} bonne(s) affaire(s).")


if __name__ == "__main__":
    run()
