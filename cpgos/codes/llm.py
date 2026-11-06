"""A language model as the reader: prompted with category definitions only, or fine-tuned.

The definitions describe each category in plain words and never use a held-out phrasing (a test checks
this), so a model that gets "OTIF FINE" right does so from what it already knows, not from the prompt.
"""

from __future__ import annotations

import re

from cpgos.codes.data import CATEGORIES, FAMILIES

PROMPT_VERSION = "codes-prompt-1.0.0"

DEFINITIONS = {
    "shortage": "fewer units or cases arrived than were invoiced",
    "late_fee": "a charge because a delivery arrived late or outside its agreed window",
    "scan_back": "a promotion paid per unit sold at the register during a price promotion",
    "spoils": "an allowance for product that was damaged, unsellable, or past its date",
    "pricing": "the price billed differs from the agreed price or cost",
    "coupon": "the retailer recovers the value of manufacturer coupons customers used",
    "advertising": "a fee for advertising the product in the retailer's ads",
    "return": "product sent back to the supplier",
    "display": "a fee for placing the product on a special display",
    "slotting": "a fee for adding a new item to the retailer's shelves or warehouse",
}
SYSTEM = ("You classify deduction reasons from retailer remittances into exactly one category. "
          "Answer with the category name only.")


def prompt(text: str) -> str:
    lines = "\n".join(f"- {c}: {DEFINITIONS[c]}" for c in CATEGORIES)
    return f"Categories:\n{lines}\n\nDeduction reason: {text}\nCategory:"


def short_prompt(text: str) -> str:
    """For fine-tuning: the model learns the categories from labelled examples, so the definitions are not
    needed, and leaving them out makes each example about a quarter as long (lora-1.0 crawled on a laptop)."""
    return f"Categories: {', '.join(CATEGORIES)}\nDeduction reason: {text}\nCategory:"


def parse(answer: str) -> str | None:
    """The first category name in the answer (models sometimes add punctuation or a sentence)."""
    a = answer.lower().replace("-", "_").replace(" ", "_")
    hits = [(a.find(c), c) for c in CATEGORIES if c in a]
    return min(hits)[1] if hits else None


def heldout_phrases_in_prompt() -> list[str]:
    """Held-out wording that leaked into the prompt (must be empty)."""
    text = (SYSTEM + prompt("x")).upper()
    words = {w for c in CATEGORIES for fam in FAMILIES[c][1] for w in re.findall(r"[A-Z&]{3,}", fam)}
    seen = {w for c in CATEGORIES for fam in FAMILIES[c][0] for w in re.findall(r"[A-Z&]{3,}", fam)}
    return sorted(w for w in words - seen if re.search(rf"\b{re.escape(w)}\b", text))


def prompted(provider):
    from cpgos.llm import LLMRequest

    def predict(text: str) -> str | None:
        r = provider.complete(LLMRequest(system=SYSTEM, prompt=prompt(text), temperature=0.0, max_tokens=8))
        return parse(r.text)
    return predict
