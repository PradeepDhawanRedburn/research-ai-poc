"""Cost per question from a price table, never from a guess.

prices.yaml at the repo root (PRICES_FILE):

    currency: GBP
    prices:
      gpt-5-mini: {input: 0.20, output: 1.60}      # per million tokens
      text-embedding-3-large: {input: 0.10}

An answer costs its answer-model input and output tokens at that deployment's prices, plus its vision tokens.
Vision tokens are recorded in and out together, so they are priced at the vision deployment's input rate, which
understates a little (output tokens are dearer but few). When a deployment has no price the cost is None and the
reports say "not priced"; no number is invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import settings
from ..schema import Answer


@dataclass(frozen=True)
class Price:
    input: float   # per million tokens
    output: float  # per million tokens


class PriceTable:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.prices_file)
        self.currency = settings.currency
        self.prices: dict[str, Price] = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self.currency = data.get("currency") or self.currency
            for name, p in (data.get("prices") or {}).items():
                if not isinstance(p, dict) or p.get("input") in (None, ""):
                    continue
                self.prices[str(name)] = Price(float(p["input"]), float(p.get("output") or 0.0))

    def has(self, deployment: str) -> bool:
        return deployment in self.prices

    def cost(self, deployment: str, input_tokens: int, output_tokens: int = 0) -> float | None:
        p = self.prices.get(deployment)
        if p is None:
            return None
        return (input_tokens * p.input + output_tokens * p.output) / 1_000_000

    def cost_of(self, a: Answer, answer_model: str | None = None, vision_model: str | None = None) -> float | None:
        """Total cost of one answer, or None when any model it used is not priced."""
        answer_model = answer_model or a.model or settings.answer_model
        main = self.cost(answer_model, a.input_tokens, a.output_tokens)
        if main is None:
            return None
        if a.vision_tokens:
            vision = self.cost(vision_model or settings.vision_deployment, a.vision_tokens, 0)
            if vision is None:
                return None
            main += vision
        return main

    def unpriced(self, deployments: list[str]) -> list[str]:
        return [d for d in dict.fromkeys(deployments) if d and d not in self.prices]


_table: PriceTable | None = None


def prices() -> PriceTable:
    global _table
    if _table is None:
        _table = PriceTable()
    return _table


def reload_prices(path: Path | None = None) -> PriceTable:
    global _table
    _table = PriceTable(path)
    return _table


def fmt_money(value: float | None, currency: str | None = None, per: str = "") -> str:
    """'£0.0123' / '£1.23 per 100 questions' / 'not priced'."""
    if value is None:
        return "not priced"
    cur = currency or prices().currency
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(cur, cur + " ")
    digits = 4 if abs(value) < 0.1 else 2
    return f"{symbol}{value:,.{digits}f}" + (f" {per}" if per else "")
