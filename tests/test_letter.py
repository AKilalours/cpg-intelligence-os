from cpgos.agent import letter as L

CLAIM = {"claim_number": "R-0000268", "deduction_date": "2016-03-02", "amount": "412.50",
         "dispute_deadline": "2016-05-01", "claimed_units": 150}
DISPUTE = {"action": "dispute", "reason": "already claimed on R-0000120", "recoverable": "412.50",
           "facts": [{"fact": "earlier claims for the same thing", "value": ["R-0000120"]}]}
CALCS = [{"calculation_id": 7, "inputs": {"amount": "412.50", "supported": "0.00"}, "result": "412.50"}]


def test_numbers_ignore_dates_and_record_ids_but_catch_amounts():
    assert L.numbers_in("Claim R-0000268 on 2016-03-02 at store TX_1 for $1,412.50 and 3 units, 12%") == \
        {"1412.50", "3", "12"}


def test_the_template_letter_passes_its_own_check():
    out = L.draft(CLAIM, DISPUTE, CALCS, "Northfield Pantry Co.", "KeHE")
    assert out["source"] == "template" and out["unsupported"] == []
    assert "$412.50" in out["text"] and "R-0000120" in out["text"]


def test_a_model_letter_with_an_invented_number_is_rejected():
    def model(**kw):
        return "We dispute claim R-0000268. You owe us $512.50 for 150 units."     # 512.50 appears nowhere
    out = L.draft(CLAIM, DISPUTE, CALCS, "Northfield Pantry Co.", "KeHE", model=model)
    assert out["source"] == "template" and out["model_rejected"] == {"unsupported_numbers": ["512.50"], "window_problems": []}


def test_a_model_letter_whose_numbers_are_all_sourced_is_used():
    def model(**kw):
        return "We dispute claim R-0000268 ($412.50, 150 units): it repeats R-0000120."
    out = L.draft(CLAIM, DISPUTE, CALCS, "Northfield Pantry Co.", "KeHE", model=model)
    assert out["source"] == "model" and out["unsupported"] == []


def test_request_and_accept_letters():
    req = {"action": "request", "reason": "no agreement is on file", "request": "the promotion agreement", "facts": []}
    assert "the promotion agreement" in L.draft(CLAIM, req, [], "N", "K")["text"]
    assert "accept it" in L.draft(CLAIM, {"action": "accept", "reason": "", "facts": []}, [], "N", "K")["text"]



def test_a_letter_never_asks_for_anything_by_a_deadline_that_has_passed():
    # Found on the case screen: claim deadline 2015-05-06, ledger date 2016-05-22, letter said "send before 2015-05-06".
    req = {"action": "request", "reason": "no agreement is on file", "request": "the promotion agreement", "facts": []}
    late = L.draft(CLAIM, req, [], "N", "K", as_of="2016-05-22")["text"]
    assert "has passed" in late and "Please send it before" not in late
    open_ = L.draft(CLAIM, req, [], "N", "K", as_of="2016-04-01")["text"]
    assert "Please send it before 2016-05-01" in open_
    d = L.draft(CLAIM, DISPUTE, CALCS, "N", "K", as_of="2016-05-22")
    assert "has passed" in d["text"] and d["unsupported"] == []



def test_a_dispute_states_the_fact_from_the_records_and_every_number_is_sourced():
    calc = [{"calculation_id": 9, "result": "18.48", "inputs": {"engine_rule": "shortage_not_on_receipt", "engine_evidence": {
        "invoice_number": "NP1052188", "shipped_units": 24, "received_units": 24, "supported_amount": "0.00"}}}]
    f = {"action": "dispute", "reason": "the evidence does not support it", "recoverable": "18.48", "facts": []}
    c = dict(CLAIM, amount="18.48")
    out = L.draft(c, f, calc, "N", "K")
    assert "shows 24 of 24 units received, so there was no shortage" in out["text"] and out["unsupported"] == []
    assert "shortage_not_on_receipt" not in out["text"]


# ---------- letter 1.3: dates are checked, in any common form ----------

def test_a_correct_date_written_in_words_passes():
    # Local model, 2026-11-05: "The dispute deadline is September 26, 2016" was rejected as the numbers 26 and 2016.
    assert L.dates_in("deadline September 26, 2016, taken 1 Mar 2016, or 2016-05-01") == {"2016-09-26", "2016-03-01", "2016-05-01"}
    claim = dict(CLAIM, dispute_deadline="2016-09-26")
    assert L.verify("The dispute deadline is September 26, 2016. Claim R-0000268, $412.50.", claim) == []


def test_an_invented_date_is_rejected_in_any_form():
    # 1.2 skipped dates entirely: an invented deadline in YYYY-MM-DD form would have passed.
    assert L.verify("Please reply by 2016-10-26.", CLAIM) == ["2016-10-26"]
    assert L.verify("Please reply by October 26, 2016.", CLAIM) == ["2016-10-26"]


def test_request_letters_give_the_retailer_a_plain_reason():
    req = {"action": "request", "engine_rule": "scan_back_pos_incomplete", "request": "POS detail for the missing weeks",
           "reason": "the engine could not decide (scan back pos incomplete); this document closes the gap", "facts": []}
    text = L.draft(CLAIM, req, [], "N", "K")["text"]
    assert "missing weeks in the claimed period" in text and "engine" not in text


# ---------- letter 1.4: the deadline is described truthfully ----------

def test_a_letter_may_not_ask_for_anything_by_a_deadline_that_has_passed():
    # Full local-model run: R-0000268's draft passed every number check yet asked for a document
    # "by the dispute deadline of 2015-05-06", a year after it.
    claim = dict(CLAIM, dispute_deadline="2015-05-06")
    t = "Please provide the necessary document by the dispute deadline of 2015-05-06."
    assert L.window_problems(t, claim, "2016-05-22") == ["asks for action by 2015-05-06, which has passed"]
    assert L.window_problems("The dispute deadline was 2015-05-06 and has passed; we raise this for the record.",
                             claim, "2016-05-22") == []
    assert L.window_problems("The deadline has not yet passed.", claim, "2016-05-22") == ["says the deadline has not passed; it has"]
    assert L.window_problems(t, claim, "2015-04-01") == []                    # still open: asking by it is right


def test_a_model_draft_that_misstates_the_deadline_falls_back_to_the_template():
    claim = dict(CLAIM, dispute_deadline="2015-05-06")
    req = {"action": "request", "reason": "no agreement", "request": "the promotion agreement", "facts": []}
    out = L.draft(claim, req, [], "N", "K", as_of="2016-05-22",
                  model=lambda **kw: "Claim R-0000268: please send the promotion agreement by 2015-05-06.")
    assert out["source"] == "template" and out["model_rejected"]["window_problems"]
    assert out["window_problems"] == []                                        # the template itself is truthful


# ---------- letter 1.5: one deadline sentence, no instruction echoes ----------

def test_a_letter_that_echoes_its_instructions_is_rejected():
    # Prompt 1.2: "As the dispute deadline of 2015-05-06 has passed, we will not be asking for anything by that date."
    claim = dict(CLAIM, dispute_deadline="2015-05-06")
    req = {"action": "request", "reason": "no agreement", "request": "the promotion agreement", "facts": []}
    echo = "Claim R-0000268. As the deadline has passed, we will not be asking for anything by that date."
    out = L.draft(claim, req, [], "N", "K", as_of="2016-05-22", model=lambda **kw: echo)
    assert out["source"] == "template" and any("echoes" in p for p in out["model_rejected"]["window_problems"])


def test_template_and_model_share_one_true_deadline_sentence():
    claim = dict(CLAIM, dispute_deadline="2015-05-06")
    req = {"action": "request", "reason": "no agreement", "request": "the promotion agreement", "facts": []}
    s = L.deadline_sentence(claim, req, "2016-05-22")
    assert s == "The dispute deadline (2015-05-06) has passed, so we ask for it for our records."
    assert s in L.draft(claim, req, [], "N", "K", as_of="2016-05-22")["text"]
    good = f"Claim R-0000268: we need the promotion agreement. {s}"
    assert L.draft(claim, req, [], "N", "K", as_of="2016-05-22", model=lambda **kw: good)["source"] == "model"
