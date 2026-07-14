"""
Détection de rupture de tendance ("breakout") plus robuste que la comparaison
naïve ancien-vs-récent sur les 5 derniers points (utilisée initialement dans
trend_detector.py - sensible à un seul prix aberrant).

Principe : on compare la moyenne des jours récents (ex: 2 derniers jours) à
une base de référence sur une fenêtre antérieure (ex: les 10 jours d'avant,
en excluant la fenêtre récente elle-même pour ne pas polluer la référence).
Un point isolé aberrant a beaucoup moins d'impact sur une moyenne de fenêtre
que sur une comparaison point-à-point.
"""
from datetime import datetime, timedelta, timezone

from db import get_price_history_asc

RECENT_WINDOW_DAYS = 2
BASELINE_WINDOW_DAYS = 10
MIN_BASELINE_POINTS = 3
MIN_RECENT_POINTS = 1


def _parse_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def compute_breakout(card_name: str, source: str,
                      recent_window_days: int = RECENT_WINDOW_DAYS,
                      baseline_window_days: int = BASELINE_WINDOW_DAYS):
    """Retourne un dict avec change_pct, recent_avg, baseline_avg si assez de
    données sont disponibles pour comparer, sinon None."""
    history = get_price_history_asc(card_name, source, limit=500)
    if not history:
        return None

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=recent_window_days)
    baseline_cutoff = recent_cutoff - timedelta(days=baseline_window_days)

    recent_prices = []
    baseline_prices = []

    for row in history:
        dt = _parse_dt(row["fetched_at"])
        price = row["price_eur"]
        if price is None:
            continue
        if dt >= recent_cutoff:
            recent_prices.append(price)
        elif dt >= baseline_cutoff:
            baseline_prices.append(price)

    if len(recent_prices) < MIN_RECENT_POINTS or len(baseline_prices) < MIN_BASELINE_POINTS:
        return None

    recent_avg = sum(recent_prices) / len(recent_prices)
    baseline_avg = sum(baseline_prices) / len(baseline_prices)

    if baseline_avg == 0:
        return None

    change_pct = ((recent_avg - baseline_avg) / baseline_avg) * 100

    return {
        "card_name": card_name,
        "source": source,
        "recent_avg": round(recent_avg, 2),
        "baseline_avg": round(baseline_avg, 2),
        "change_pct": round(change_pct, 1),
        "n_recent": len(recent_prices),
        "n_baseline": len(baseline_prices),
    }
