# Phase 3: the investigation agent

The engine (Phase 2a) checks every claim against the evidence. When the evidence is damaged it
either stops ("needs evidence") or, rarely, disputes a valid claim because a record is missing.
The investigator goes through the engine's work the way a senior AR analyst would.

## What it may see

Only what a supplier would really have, enforced by the database, not by a prompt:

| Can read | Cannot read |
|---|---|
| The damaged evidence views (`observed.*`): POS as provided, receipts, shipments, promotions on file | The undamaged originals under the overlay (0019) |
| Invoices, terms, earlier claims and the engine's assessments | Each claim's true store (0019) |
| Weekly shelf prices (store checks, syndicated data) | Units in the M5 sales table, which are the true POS (0020) |
| | The answer key (`eval.*`) and the damage report |

It can write only cases, audit events, and calculation rows. It cannot change a deduction, an
invoice, or a cash posting (tested).

## Playbooks

| Engine result | Investigator |
|---|---|
| Valid | Accept |
| Not supported, evidence complete | Dispute, with the calculation behind the amount and any earlier claim it repeats |
| No agreement on file | If the shelf price dropped like a promotion in the claimed weeks (at or below 90% of the prior 8-week high), request the agreement instead of disputing; otherwise dispute |
| Over-claim past the recorded promotion end | If the price stayed promotional after the recorded end, request the amended agreement; otherwise dispute |
| No store on the claim | If exactly one store ran a promotion for the item then, check the claim against that store's POS; otherwise request the store |
| POS weeks, receipt, or delivery record missing | Request that document |

A price drop can turn a dispute into a request. It can never turn anything into an acceptance.

## How it is scored

Designed on California stores (`make agent-design`, rerunnable, shows the truth for California
only). Scored once per agent version on Texas and Wisconsin (`make agent-score`, which refuses a
second run of the same version). Outcomes: false disputes, false approvals, disputes exact to the
cent, dollars disputed correctly, and requests, split into requests for claims that turned out
valid (a false dispute prevented) and invalid (a slower dispute).
