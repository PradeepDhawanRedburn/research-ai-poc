"""Offline distribution report for digitised chart points paired with native values."""
from __future__ import annotations

import math
import re
import statistics

from ..storage import FACTS, Store


def _x_key(value) -> str:
    text = str(value).strip().casefold()
    match = re.match(r"^(\d{4}-\d{2})(?:-\d{2})?$", text)
    return match.group(1) if match else text


def _paired(facts: list[dict]) -> tuple[list[float], set[tuple]]:
    native = {}
    digitised = []
    for figure in facts:
        key = (figure.get("doc_id"), figure.get("figure_id") or figure.get("figure"),
               " ".join((figure.get("caption") or "").lower().split()))
        if figure.get("kind") == "native_chart":
            native.setdefault(key, []).extend(figure.get("series", []))
        elif figure.get("kind") == "digitised_chart":
            digitised.append((key, figure))
    errors = []
    matched_charts = set()
    for key, figure in digitised:
        for series in figure.get("series", []):
            name = (series.get("name") or "").strip().casefold()
            span = float(series.get("axis_range") or 0)
            if not name or not span:
                continue
            native_series = next((s for s in native.get(key, [])
                                  if (s.get("name") or "").strip().casefold() == name), None)
            if native_series is None:
                continue
            native_points = {_x_key(x): float(y) for x, y in native_series.get("points", [])
                             if isinstance(y, (int, float))}
            for x, y in series.get("points", []):
                point_key = _x_key(x)
                if point_key in native_points and isinstance(y, (int, float)):
                    errors.append(abs(float(y) - native_points[point_key]) / span * 100)
                    matched_charts.add(key)
    return errors, matched_charts


def paired_errors(facts: list[dict]) -> list[float]:
    return _paired(facts)[0]


def distribution(errors: list[float]) -> dict:
    ordered = sorted(errors)
    if not ordered:
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {"n": len(ordered), "median": statistics.median(ordered),
            "p90": ordered[math.ceil(0.9 * len(ordered)) - 1], "max": ordered[-1]}


def report(store: Store) -> str:
    facts = []
    for key in store.keys(f"{FACTS}/", ".json"):
        rows = store.json(key)
        facts.extend(rows if isinstance(rows, list) else rows.get("figures", []))
    errors, charts = _paired(facts)
    stats = distribution(errors)
    if not stats["n"]:
        return ("Native-versus-digitised point error (% of digitised axis range)\n"
            f"Matched charts: {len(charts)}\nMatched points: 0\nMedian: N/A\nP90: N/A\nMax: N/A\n"
            "No same-series, same-category native/digitised point pairs were available in the facts store.")
    return ("Native-versus-digitised point error (% of digitised axis range)\n"
            f"Matched charts: {len(charts)}\nMatched points: {stats['n']}\nMedian: {stats['median']:.3f}%\n"
            f"P90: {stats['p90']:.3f}%\nMax: {stats['max']:.3f}%")