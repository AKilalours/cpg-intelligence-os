"""A hard spending cap for paid model calls.

Analogy: a prepaid card. Before each paid call the worst-case cost (the
input, plus the full output allowance) is checked against what is left; a
call that could overspend is refused before it is sent. After the call, the
real cost from the provider's reported token counts is written to a ledger.

Prices are per million tokens, from Anthropic's pricing page
(https://platform.claude.com/docs/en/about-claude/pricing, checked
2026-10-30). Override with CPGOS_PRICE_<MODEL>_IN / _OUT if they change.
The cap is CPGOS_LLM_BUDGET_USD (default 5.00 dollars) for the whole ledger.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from cpgos import config

LEDGER = Path(__file__).resolve().parents[2] / "data" / "processed" / "llm_spend.jsonl"
PRICES_PER_MTOK = {                     # (input, output) in USD per million tokens
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}
DEFAULT_CAP = Decimal("5.00")
IMAGE_TOKENS_PER_PIXEL = Decimal(1) / Decimal(750)   # documented estimate: width x height / 750


class BudgetExceeded(RuntimeError):
    pass


def price(model: str) -> tuple[Decimal, Decimal]:
    key = model.upper().replace("-", "_").replace(".", "_")
    pin, pout = config.get(f"CPGOS_PRICE_{key}_IN"), config.get(f"CPGOS_PRICE_{key}_OUT")
    if pin and pout:
        return Decimal(pin), Decimal(pout)
    if model not in PRICES_PER_MTOK:
        raise BudgetExceeded(f"no price known for {model}; set CPGOS_PRICE_{key}_IN and _OUT before any paid call")
    return PRICES_PER_MTOK[model]


def cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    pin, pout = price(model)
    return (pin * input_tokens + pout * output_tokens) / Decimal(1_000_000)


def cap(ledger: Path = LEDGER) -> Decimal:
    return Decimal(config.get("CPGOS_LLM_BUDGET_USD") or DEFAULT_CAP)


def spent(ledger: Path = LEDGER) -> Decimal:
    if not ledger.is_file():
        return Decimal(0)
    return sum((Decimal(json.loads(line)["usd"]) for line in ledger.read_text().splitlines() if line.strip()), Decimal(0))


def check(model: str, est_input_tokens: int, max_output_tokens: int, ledger: Path = LEDGER) -> Decimal:
    """Refuse a call whose worst case would exceed the cap. Returns the worst-case cost."""
    worst = cost(model, est_input_tokens, max_output_tokens)
    left = cap(ledger) - spent(ledger)
    if worst > left:
        raise BudgetExceeded(f"budget cap ${cap(ledger)} reached: ${spent(ledger):.4f} spent, this call could cost "
                             f"${worst:.4f}. Raise CPGOS_LLM_BUDGET_USD in .env to continue.")
    return worst


def record(model: str, input_tokens: int, output_tokens: int, note: str = "", ledger: Path = LEDGER) -> Decimal:
    usd = cost(model, input_tokens or 0, output_tokens or 0)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": model, "input_tokens": input_tokens,
                            "output_tokens": output_tokens, "usd": str(usd), "note": note}) + "\n")
    return usd


def estimate_image_tokens(width: int, height: int) -> int:
    return int((Decimal(width * height) * IMAGE_TOKENS_PER_PIXEL).to_integral_value()) + 1
