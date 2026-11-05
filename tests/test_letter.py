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
    assert out["source"] == "template" and out["model_rejected"] == {"unsupported_numbers": ["512.50"]}


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
