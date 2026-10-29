-- 0002: canonical CPG data model.
-- Money columns are NUMERIC (exact decimal), never float.
-- Amounts are stored in the currency's major unit (dollars) with 2 decimals;
-- unit prices allow 4 decimals because retailer price files do.

CREATE TABLE brand (
    brand_id        BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    currency        CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$')
);

-- Retailers and distributors are both trading partners: KeHE and UNFI pay
-- the brand and also pass through deductions taken by the stores they serve.
CREATE TABLE trading_partner (
    partner_id      BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    partner_type    TEXT NOT NULL CHECK (partner_type IN ('retailer', 'distributor')),
    UNIQUE (name, partner_type)
);

-- External identifiers are NOT trusted to be unique or correct across sources.
-- Entity resolution writes here with its method and confidence, so every
-- link can be audited and undone.
CREATE TABLE partner_external_id (
    partner_external_id_id BIGSERIAL PRIMARY KEY,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    source_system   TEXT NOT NULL,                  -- e.g. 'm5', 'kehe_remit', 'docile'
    external_id     TEXT NOT NULL,
    resolution_method TEXT NOT NULL CHECK (resolution_method IN ('exact', 'rule', 'model', 'human')),
    confidence      NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    UNIQUE (source_system, external_id)
);

CREATE TABLE store (
    store_id        BIGSERIAL PRIMARY KEY,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    external_code   TEXT NOT NULL,                  -- e.g. M5 'CA_1'
    region          TEXT,
    UNIQUE (partner_id, external_code)
);

CREATE TABLE product (
    sku_id          BIGSERIAL PRIMARY KEY,
    brand_id        BIGINT REFERENCES brand,
    sku_code        TEXT NOT NULL UNIQUE,
    description     TEXT,
    category        TEXT,
    department      TEXT,
    unit_cost       NUMERIC(14,4) CHECK (unit_cost >= 0),
    list_price      NUMERIC(14,4) CHECK (list_price >= 0)
);

CREATE TABLE product_identifier (
    product_identifier_id BIGSERIAL PRIMARY KEY,
    sku_id          BIGINT NOT NULL REFERENCES product,
    id_type         TEXT NOT NULL CHECK (id_type IN ('gtin', 'upc', 'partner_item', 'source_item')),
    id_value        TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    resolution_method TEXT NOT NULL CHECK (resolution_method IN ('exact', 'rule', 'model', 'human')),
    confidence      NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    UNIQUE (id_type, id_value, source_system)
);

CREATE TABLE invoice (
    invoice_id      BIGSERIAL PRIMARY KEY,
    invoice_number  TEXT NOT NULL,
    brand_id        BIGINT NOT NULL REFERENCES brand,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    po_number       TEXT,
    invoice_date    DATE NOT NULL,
    due_date        DATE,
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    total_amount    NUMERIC(14,2) NOT NULL CHECK (total_amount >= 0),
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'partially_paid', 'paid', 'disputed', 'written_off')),
    source_document_id BIGINT REFERENCES source_document,
    UNIQUE (brand_id, partner_id, invoice_number),
    CHECK (due_date IS NULL OR due_date >= invoice_date)
);

CREATE TABLE invoice_line (
    invoice_line_id BIGSERIAL PRIMARY KEY,
    invoice_id      BIGINT NOT NULL REFERENCES invoice ON DELETE CASCADE,
    line_number     INTEGER NOT NULL CHECK (line_number > 0),
    sku_id          BIGINT REFERENCES product,
    raw_description TEXT,                           -- kept even when SKU resolution fails
    quantity        NUMERIC(14,3) NOT NULL CHECK (quantity >= 0),
    unit_price      NUMERIC(14,4) NOT NULL CHECK (unit_price >= 0),
    discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    line_amount     NUMERIC(14,2) NOT NULL,
    UNIQUE (invoice_id, line_number)
);

CREATE TABLE payment (
    payment_id      BIGSERIAL PRIMARY KEY,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    payment_reference TEXT NOT NULL,                -- check / ACH trace number
    payment_date    DATE NOT NULL,
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    amount          NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    source_document_id BIGINT REFERENCES source_document,
    UNIQUE (partner_id, payment_reference)
);

-- What the payer SAID each part of the payment was for (raw, untrusted).
CREATE TABLE remittance_line (
    remittance_line_id BIGSERIAL PRIMARY KEY,
    payment_id      BIGINT NOT NULL REFERENCES payment ON DELETE CASCADE,
    line_number     INTEGER NOT NULL,
    raw_invoice_ref TEXT,                           -- as written by the payer, may be wrong
    raw_code        TEXT,                           -- e.g. KeHE prefix on a deduction line
    gross_amount    NUMERIC(14,2),
    deduction_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    net_amount      NUMERIC(14,2) NOT NULL,
    UNIQUE (payment_id, line_number)
);

-- What WE decided each part of the payment applies to (cash application output).
CREATE TABLE cash_application (
    cash_application_id BIGSERIAL PRIMARY KEY,
    remittance_line_id BIGINT NOT NULL REFERENCES remittance_line,
    invoice_id      BIGINT NOT NULL REFERENCES invoice,
    applied_amount  NUMERIC(14,2) NOT NULL CHECK (applied_amount > 0),
    method          TEXT NOT NULL CHECK (method IN ('exact', 'rule', 'model', 'human')),
    confidence      NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    match_explanation JSONB NOT NULL,               -- which signals matched, so a reviewer can see why
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected', 'reversed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Published deduction code dictionaries (e.g. KeHE invoice prefixes).
CREATE TABLE deduction_code (
    deduction_code_id BIGSERIAL PRIMARY KEY,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    code            TEXT NOT NULL,
    description     TEXT NOT NULL,
    code_family     TEXT NOT NULL CHECK (code_family IN (
                        'promotion', 'pricing', 'shortage', 'compliance', 'spoils_returns',
                        'adjustment', 'other')),
    source_url      TEXT NOT NULL,                  -- where the definition was published
    UNIQUE (partner_id, code)
);

CREATE TABLE promotion (
    promotion_id    BIGSERIAL PRIMARY KEY,
    brand_id        BIGINT NOT NULL REFERENCES brand,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    promo_type      TEXT NOT NULL CHECK (promo_type IN ('off_invoice', 'bill_back', 'scan_back', 'tpr', 'feature', 'display', 'coupon', 'other')),
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    discount_per_unit NUMERIC(14,4),
    discount_pct    NUMERIC(6,4) CHECK (discount_pct IS NULL OR discount_pct BETWEEN 0 AND 1),
    planned_spend   NUMERIC(14,2) CHECK (planned_spend IS NULL OR planned_spend >= 0),
    source_document_id BIGINT REFERENCES source_document,
    CHECK (end_date >= start_date)
);

CREATE TABLE promotion_sku (
    promotion_id    BIGINT NOT NULL REFERENCES promotion ON DELETE CASCADE,
    sku_id          BIGINT NOT NULL REFERENCES product,
    PRIMARY KEY (promotion_id, sku_id)
);

CREATE TABLE deduction (
    deduction_id    BIGSERIAL PRIMARY KEY,
    claim_number    TEXT NOT NULL,
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    remittance_line_id BIGINT REFERENCES remittance_line,
    deduction_code_id BIGINT REFERENCES deduction_code,
    raw_reason      TEXT,
    amount          NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    deduction_date  DATE NOT NULL,
    dispute_deadline DATE,                          -- from the partner's published dispute window
    raw_invoice_ref TEXT,
    invoice_id      BIGINT REFERENCES invoice,      -- resolved link, may be NULL
    promotion_id    BIGINT REFERENCES promotion,    -- supporting agreement, if found
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
                        'open', 'investigating', 'valid_accepted', 'disputed',
                        'recovered', 'denied', 'written_off', 'expired')),
    UNIQUE (partner_id, claim_number),
    CHECK (dispute_deadline IS NULL OR dispute_deadline >= deduction_date)
);

-- Weekly or daily sell-through. Real data (M5, Favorita, dunnhumby) lands here.
CREATE TABLE sales_observation (
    sales_observation_id BIGSERIAL PRIMARY KEY,
    obs_date        DATE NOT NULL,
    granularity     TEXT NOT NULL CHECK (granularity IN ('day', 'week')),
    sku_id          BIGINT NOT NULL REFERENCES product,
    store_id        BIGINT NOT NULL REFERENCES store,
    units           NUMERIC(14,3) NOT NULL CHECK (units >= 0),
    shelf_price     NUMERIC(14,4) CHECK (shelf_price >= 0),
    base_price      NUMERIC(14,4) CHECK (base_price >= 0),
    -- Promotion signal and WHERE it came from. M5 has no promo flag, so a
    -- price-drop flag must be labeled 'derived_price_drop', never 'observed'.
    promo_flag      BOOLEAN,
    promo_flag_source TEXT CHECK (promo_flag_source IN ('observed', 'derived_price_drop')),
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version,
    UNIQUE (obs_date, granularity, sku_id, store_id),
    CHECK ((promo_flag IS NULL) = (promo_flag_source IS NULL))
);

CREATE INDEX sales_obs_series_idx ON sales_observation (sku_id, store_id, obs_date);
CREATE INDEX deduction_status_idx ON deduction (status, dispute_deadline);
