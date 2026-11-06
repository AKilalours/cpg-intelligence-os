"""Realistic, messy deduction reason text, with held-out phrasings for an honest score.

What it is: retailer and distributor remittances describe a deduction in free text, written by many
people and systems: "SHRT 2CS PO#44718", "late dlvry fee wk34", "OTIF FINE". The code beside it is
often too coarse (KeHE's IA covers shortages, late fees, and price differences alike), so the reason
text decides which check runs.

The vocabulary is industry terminology (OS&D, OTIF, MCB, TPR, billback, swell, RTV, IRC, PPV, slotting).
Each category has several phrase families. Some families exist only in the held-out set, so a model
is scored on wordings it never saw: "OTIF FINE" for a late fee when training only had "LATE DELIVERY",
"SWELL ALLOWANCE" for spoils when training only had "DAMAGED". A model that memorised words fails there;
one that understands the domain does not.

This is generated text, not real remittances. The held-out families are what keep it from being a
self-graded test, and the README says so.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DATA_VERSION = "reasons-1.1.0"   # 1.1: a second held-out set, written before any reader was scored on it

# category -> (train families, held-out families). A family is one way of saying it.
FAMILIES: dict[str, tuple[list[str], list[str]]] = {
    "shortage": (["SHORTAGE", "SHORT SHIP", "SHORTED CASES", "QTY SHORT", "SHORT RECEIVED", "RECEIVING SHORTAGE"],
                 ["OS&D SHORT", "CASES NOT RECEIVED", "MISSING CASES"]),
    "late_fee": (["LATE DELIVERY FEE", "LATE SHIPMENT", "DELIVERY WINDOW MISSED", "LATE ARRIVAL CHARGE"],
                 ["OTIF FINE", "ON TIME IN FULL PENALTY", "APPT MISS FEE"]),
    "scan_back": (["PROMO SCANBACK", "SCAN BACK ALLOWANCE", "SCAN DOWN", "PROMO SCAN ALLOWANCE"],
                  ["TPR BILLBACK", "TEMP PRICE REDUCTION", "MCB PROMO"]),
    "spoils": (["SPOILS", "SPOILS ALLOWANCE", "DAMAGED PRODUCT", "UNSALEABLES"],
               ["SWELL ALLOWANCE", "OUT OF CODE", "EXPIRED PRODUCT"]),
    "pricing": (["PRICE DIFFERENCE", "PRICE PROTECTION", "COST VARIANCE", "PRICE CORRECTION"],
                ["INVOICE OVERCHARGE", "PRICE DISCREPANCY", "PPV"]),
    "coupon": (["COUPON", "MFR COUPON", "COUPON REDEMPTION", "COUPON CLEARING"],
               ["DIGITAL CPN", "INSTANT REDEEMABLE", "IRC"]),
    "advertising": (["AD FEE", "ADVERTISING", "CIRCULAR", "AD ALLOWANCE"],
                    ["FLYER PLACEMENT", "WEEKLY AD", "FEATURE AD"]),
    "return": (["VENDOR RETURN", "RTV", "RETURN TO VENDOR", "RETURNED GOODS"],
               ["RECALL CREDIT", "DISCONTINUED ITEM RETURN", "RA CREDIT"]),
    "display": (["DISPLAY FEE", "END CAP", "DISPLAY ALLOWANCE"],
                ["SHIPPER DISPLAY", "PALLET DISPLAY", "PROMO DISPLAY"]),
    "slotting": (["SLOTTING", "NEW ITEM FEE", "SLOTTING ALLOWANCE"],
                 ["INTRO FEE", "NEW DISTRIBUTION FEE", "PLACEMENT FEE"]),
}
CATEGORIES = sorted(FAMILIES)

# A second set of unseen phrasings, written on 2026-11-05 after the first held-out set had been read (and so
# had become a development set) and before any reader was scored on this one. New designs are scored here, once.
HELDOUT2: dict[str, list[str]] = {
    "shortage": ["UNDER SHIPPED", "SHORT ON BOL", "SHORT PICK"],
    "late_fee": ["MISSED DELIVERY APPOINTMENT", "LATE TRUCK FEE", "OTD PENALTY"],
    "scan_back": ["PROMOTIONAL ALLOWANCE", "BILLBACK PROMO", "PRICE ROLLBACK FUNDING"],
    "spoils": ["DEFECTIVE ALLOWANCE", "MARKDOWN FOR DAMAGE", "STALE DATED"],
    "pricing": ["COST DISCREPANCY", "PRICE MISMATCH", "OVERBILLED"],
    "coupon": ["CATALINA", "COUPON REIMBURSEMENT", "MFG OFFER REDEEMED"],
    "advertising": ["CO-OP ADVERTISING", "RADIO SPOT", "IN-STORE MEDIA"],
    "return": ["DEFECTIVE RETURN", "OVERSTOCK RETURN", "RETURN AUTHORIZATION"],
    "display": ["ENDCAP PLACEMENT", "FLOOR STAND", "WING PANEL"],
    "slotting": ["LISTING FEE", "SHELF SPACE FEE", "ITEM SETUP FEE"],
}

ABBREV = {   # how remittance systems shorten words
    "ALLOWANCE": ["ALLOW", "ALW", "ALLOWNC"], "DELIVERY": ["DLVRY", "DELIV", "DEL"], "SHORTAGE": ["SHRTG", "SHORTG"],
    "RECEIVED": ["RCVD", "REC'D", "RECV"], "RECEIVING": ["RCVG", "RECV"], "PRODUCT": ["PROD"], "DIFFERENCE": ["DIFF"],
    "PROTECTION": ["PROT"], "COUPON": ["CPN", "COUP"], "ADVERTISING": ["ADV", "ADVTG"], "RETURN": ["RTN", "RET"],
    "DISPLAY": ["DSPLY", "DISP"], "SHIPMENT": ["SHPMT", "SHIP"], "CORRECTION": ["CORR"], "REDEMPTION": ["REDEMP"],
    "CHARGE": ["CHG"], "PROMO": ["PRMO"], "CASES": ["CS", "CSE"], "VENDOR": ["VNDR"], "QTY": ["QUANTITY"],
}


@dataclass(frozen=True)
class Example:
    text: str
    category: str
    family: str
    split: str          # train | validation (seen families) | heldout (unseen; now a development set) | heldout2


def _noisy(rng: random.Random, phrase: str) -> str:
    words = phrase.split()
    words = [rng.choice(ABBREV[w]) if w in ABBREV and rng.random() < 0.45 else w for w in words]
    t = " ".join(words)
    if rng.random() < 0.25 and len(t) > 5:                      # a keying error: drop, swap, or double a letter
        i = rng.randrange(1, len(t) - 1)
        t = rng.choice([t[:i] + t[i + 1:], t[:i] + t[i + 1] + t[i] + t[i + 2:], t[:i] + t[i] + t[i:]])
    affix = rng.random()
    if affix < 0.20:
        t = f"{t} INV {rng.choice(['NP', 'INV', '#'])}{rng.randint(100000, 9999999)}"
    elif affix < 0.35:
        t = f"PO#{rng.randint(1000, 99999)} {t}"
    elif affix < 0.50:
        t = f"{t} STR {rng.randint(1, 5999):04d}"
    elif affix < 0.60:
        t = f"WK{rng.randint(1, 52)} {t}"
    elif affix < 0.68:
        t = f"{t} {rng.randint(1, 12)}/{rng.randint(1, 28)}"
    elif affix < 0.74:
        t = f"{t} {rng.randint(1, 40)}CS"
    case = rng.random()
    if case < 0.30:
        t = t.lower()
    elif case < 0.45:
        t = t.title()
    if rng.random() < 0.15:
        t = t.replace(" ", rng.choice(["-", "_", "  ", "/"]), 1)
    return t


def generate(seed: int = 12, train: int = 3000, validation: int = 400, heldout: int = 800,
             heldout2: int = 800) -> list[Example]:
    """Fixed by the seed. Train and validation share families (validation: new instances); held-out uses only
    families training never sees; heldout2 uses a second unseen set. Categories are balanced within each split.
    heldout2 is drawn last from its own generator, so the first three splits are exactly those of 1.0."""
    rng = random.Random(seed)
    out: list[Example] = []
    for split, n in (("train", train), ("validation", validation), ("heldout", heldout)):
        for i in range(n):
            cat = CATEGORIES[i % len(CATEGORIES)]
            seen, unseen = FAMILIES[cat]
            fam = rng.choice(unseen if split == "heldout" else seen)
            out.append(Example(_noisy(rng, fam), cat, fam, split))
    rng2 = random.Random(seed * 1000 + 2)
    for i in range(heldout2):
        cat = CATEGORIES[i % len(CATEGORIES)]
        fam = rng2.choice(HELDOUT2[cat])
        out.append(Example(_noisy(rng2, fam), cat, fam, "heldout2"))
    return out
