"""
Compile prices.db + product_catalog.json + recent_deals.json en un seul
fichier docs/data.json, lu par le dashboard statique (docs/index.html).

Pourquoi un export statique plutôt qu'un serveur : GitHub Pages ne sert que
des fichiers statiques (pas de Python/SQLite en live), donc on regénère ce
JSON à chaque run du bot et on le commit avec le reste des données.
"""
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from db import get_connection, get_all_tracked_cards, get_latest_cardmarket_official_price, get_cardmarket_official_history
from trend_detector import compute_trend, compute_cross_source_gap

ROOT = Path(__file__).parent
DEALS_PATH = ROOT / "recent_deals.json"
META_SIGNALS_PATH = ROOT / "meta_signals.json"
PERFORMANCE_SIGNALS_PATH = ROOT / "performance_signals.json"
WEB_SIGNALS_PATH = ROOT / "web_signals.json"
CATALOG_PATH = ROOT / "product_catalog.json"
OUTPUT_PATH = ROOT / "docs" / "data.json"

_catalog_by_name = None


def load_catalog_by_name() -> dict:
    """Index product_catalog.json par nom de carte (au lieu de productId),
    pour retrouver rapidement le slug CardNexus d'une carte donnée."""
    global _catalog_by_name
    if _catalog_by_name is not None:
        return _catalog_by_name
    _catalog_by_name = {}
    if CATALOG_PATH.exists():
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        for info in data.values():
            name = info.get("name")
            if name:
                _catalog_by_name[name] = info
    return _catalog_by_name


def build_card_links(card_name: str) -> dict:
    """Lien Cardmarket (recherche, toujours dispo) + lien CardNexus (précis
    si on a le slug en catalogue, sinon page d'accueil de recherche)."""
    cardmarket_url = f"https://www.cardmarket.com/en/Magic/Products/Search?searchString={quote(card_name)}"

    catalog = load_catalog_by_name()
    info = catalog.get(card_name)
    if info and info.get("slug") and info.get("expansionSlug"):
        cardnexus_url = f"https://cardnexus.com/fr/explore/mtg/{info['expansionSlug']}/card/{info['slug']}"
    else:
        cardnexus_url = f"https://cardnexus.com/fr/explore/mtg?search={quote(card_name)}"

    return {"cardmarket_url": cardmarket_url, "cardnexus_url": cardnexus_url}


def get_history_series(card_name: str, source: str, limit: int = 60):
    conn = get_connection()
    rows = conn.execute(
        "SELECT price_eur, fetched_at FROM price_history "
        "WHERE card_name = ? AND source = ? ORDER BY fetched_at ASC LIMIT ?",
        (card_name, source, limit),
    ).fetchall()
    conn.close()
    return [{"date": r["fetched_at"], "price": r["price_eur"]} for r in rows]


def compute_volatility_pct(history: list) -> float | None:
    """Écart-type / moyenne, en % - repère les cartes dont le prix bouge beaucoup."""
    prices = [p["price"] for p in history if p["price"] is not None]
    if len(prices) < 3:
        return None
    mean = statistics.mean(prices)
    if mean == 0:
        return None
    stdev = statistics.stdev(prices)
    return round((stdev / mean) * 100, 1)


def days_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 1)


def compute_cn_window_stats(cardnexus_history: list, window: int = 7):
    """CardNexus donne un seul prix par jour (méthodologie non documentée -
    probablement pas un vrai "prix le plus bas"). On calcule ici uniquement
    une moyenne sur 7 jours de cette donnée brute. Le vrai "Low CN" (prix de
    l'annonce la moins chère réellement active) nécessite un autre endpoint,
    pas encore branché - cf. real_low / real_low7 ci-dessous, à None pour
    l'instant."""
    recent = [p["price"] for p in cardnexus_history[-window:] if p["price"] is not None]
    if not recent:
        return {"cn_latest": None, "cn_avg7": None}
    return {
        "cn_latest": cardnexus_history[-1]["price"],
        "cn_avg7": round(statistics.mean(recent), 2),
    }


def pct_diff(a: float | None, b: float | None) -> float | None:
    """Écart en % de a par rapport à b : (a - b) / b * 100."""
    if a is None or b is None or b == 0:
        return None
    return round(((a - b) / b) * 100, 1)


def abs_diff(a: float | None, b: float | None) -> float | None:
    """Écart brut en euros : a - b."""
    if a is None or b is None:
        return None
    return round(a - b, 2)


def build_cm_history_series(card_name: str, finish: str) -> list[dict]:
    """Historique complet Low/Avg/Trend Cardmarket officiel, pour le graphique
    détaillé au clic sur une ligne."""
    suffix = "_foil" if finish == "foil" else ""
    rows = get_cardmarket_official_history(card_name)
    return [
        {
            "date": r["fetched_at"],
            "low": r[f"low{suffix}"],
            "avg": r[f"avg{suffix}"],
            "trend": r[f"trend{suffix}"],
        }
        for r in rows
    ]


def build_cardmarket_official_block(card_name: str):
    row = get_latest_cardmarket_official_price(card_name)
    if not row:
        return None
    block = {
        "low": row["low"], "avg": row["avg"], "trend": row["trend"],
        "avg1": row["avg1"], "avg7": row["avg7"], "avg30": row["avg30"],
        "low_foil": row["low_foil"], "avg_foil": row["avg_foil"], "trend_foil": row["trend_foil"],
        "avg1_foil": row["avg1_foil"], "avg7_foil": row["avg7_foil"], "avg30_foil": row["avg30_foil"],
    }
    if row["avg"] and row["avg_foil"] and row["avg"] > 0:
        block["foil_premium"] = round(row["avg_foil"] / row["avg"], 1)
    else:
        block["foil_premium"] = None
    return block


def build_card_entry(card_name: str) -> dict:
    entry = {"name": card_name, "finishes": {}, "cardmarket_official": build_cardmarket_official_block(card_name)}

    for finish in ("standard", "foil"):
        cardnexus_history = get_history_series(card_name, f"cardnexus_{finish}")
        scryfall_history = get_history_series(card_name, f"scryfall_cardmarket_{finish}")
        trend = compute_trend(card_name, source=f"scryfall_cardmarket_{finish}")
        gap = compute_cross_source_gap(card_name, finish=finish)

        if not cardnexus_history and not scryfall_history:
            continue

        # prix courant + fraîcheur : priorité à la source la plus récente
        candidates = [h[-1] for h in (cardnexus_history, scryfall_history) if h]
        latest_point = max(candidates, key=lambda p: p["date"]) if candidates else None
        combined_history = sorted(cardnexus_history + scryfall_history, key=lambda p: p["date"])

        cn_stats = compute_cn_window_stats(cardnexus_history)

        # métriques Cardmarket officielles, selon la finition
        official = entry["cardmarket_official"]
        if official:
            suffix = "_foil" if finish == "foil" else ""
            cm_low = official.get(f"low{suffix}")
            cm_avg = official.get(f"avg{suffix}")
            cm_trend = official.get(f"trend{suffix}")
            cm_avg7 = official.get(f"avg7{suffix}")
        else:
            cm_low = cm_avg = cm_trend = cm_avg7 = None

        entry["finishes"][finish] = {
            "cardnexus_history": cardnexus_history,
            "scryfall_history": scryfall_history,
            "trend_pct": round(trend["change_pct"], 1) if trend else None,
            "cross_source_gap_pct": round(gap["gap_pct"], 1) if gap else None,
            "cheaper_source": gap["cheaper_source"] if gap else None,
            "current_price": latest_point["price"] if latest_point else None,
            "days_since_update": days_since(latest_point["date"]) if latest_point else None,
            "volatility_pct": compute_volatility_pct(combined_history),
            "cm_low": cm_low, "cm_avg": cm_avg, "cm_trend": cm_trend, "cm_avg7": cm_avg7,
            "cn_latest": cn_stats["cn_latest"], "cn_avg7": cn_stats["cn_avg7"],
            "low_vs_avg_pct": pct_diff(cm_low, cm_avg),
            "low_vs_avg_diff": abs_diff(cm_low, cm_avg),
            "cm_vs_cn_pct": pct_diff(cm_avg, cn_stats["cn_latest"]),
            "cm_vs_cn_diff": abs_diff(cm_avg, cn_stats["cn_latest"]),
            "cm_official_history": build_cm_history_series(card_name, finish),
        }

    return entry


def load_competitive_signals() -> list[dict]:
    """Fusionne meta_signals.json (MTGGoldfish) et performance_signals.json
    (MTGTop8) en une seule liste normalisée, triée du plus récent au plus
    ancien, dédupliquée par (nom, format, source)."""
    signals = []

    if META_SIGNALS_PATH.exists():
        signals.extend(json.loads(META_SIGNALS_PATH.read_text(encoding="utf-8")))
    if PERFORMANCE_SIGNALS_PATH.exists():
        signals.extend(json.loads(PERFORMANCE_SIGNALS_PATH.read_text(encoding="utf-8")))

    seen = set()
    deduped = []
    for s in sorted(signals, key=lambda x: x.get("detected_at", ""), reverse=True):
        key = (s.get("name"), s.get("format"), s.get("source"))
        if key in seen:
            continue
        seen.add(key)
        s["card_links"] = build_card_links(s["name"])
        deduped.append(s)

    return deduped[:150]


def load_web_signals() -> list[dict]:
    if not WEB_SIGNALS_PATH.exists():
        return []
    signals = json.loads(WEB_SIGNALS_PATH.read_text(encoding="utf-8"))
    signals = sorted(signals, key=lambda x: x.get("detected_at", ""), reverse=True)[:100]
    for s in signals:
        s["matched_card_links"] = [
            {"name": name, **build_card_links(name)} for name in s.get("matched_cards", [])[:3]
        ]
    return signals


def run():
    cards = get_all_tracked_cards()
    card_entries = [build_card_entry(name) for name in cards]
    # ne garde que les cartes avec au moins une finition ayant des données
    card_entries = [c for c in card_entries if c["finishes"]]

    recent_deals = []
    if DEALS_PATH.exists():
        recent_deals = json.loads(DEALS_PATH.read_text(encoding="utf-8"))

    competitive_signals = load_competitive_signals()
    web_signals = load_web_signals()
    competitive_card_names = {s["name"] for s in competitive_signals}
    web_card_names = set()
    for s in web_signals:
        web_card_names.update(s.get("matched_cards", []))

    # croisement : marque chaque carte suivie qui a un signal tournoi et/ou web actif
    for entry in card_entries:
        entry["has_competitive_signal"] = entry["name"] in competitive_card_names
        entry["has_web_signal"] = entry["name"] in web_card_names

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": card_entries,
        "recent_deals": recent_deals,
        "competitive_signals": competitive_signals,
        "web_signals": web_signals,
    }

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Dashboard data exporté : {len(card_entries)} carte(s), {len(recent_deals)} bonne(s) affaire(s), "
          f"{len(competitive_signals)} signal(aux) compétitif(s), {len(web_signals)} signal(aux) web.")


if __name__ == "__main__":
    run()
