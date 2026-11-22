-- Financial-Brain Phase 0 schema.
--
-- Engine note (ADR-0001): DuckDB today, PostgreSQL + TimescaleDB later. All curated
-- market data lands as Parquet, which is engine-independent, so the migration is a load
-- script rather than a rewrite. Nothing here uses DuckDB-only SQL beyond the Parquet
-- views at the bottom.

-- ---------------------------------------------------------------- provenance
-- Mirror of the raw lake's sidecar metadata, so provenance is queryable in SQL.
CREATE TABLE IF NOT EXISTS lake_manifest (
    key             VARCHAR PRIMARY KEY,
    source          VARCHAR NOT NULL,
    dataset         VARCHAR NOT NULL,
    business_date   DATE    NOT NULL,
    filename        VARCHAR NOT NULL,
    url             VARCHAR NOT NULL,
    retrieved_at    TIMESTAMPTZ NOT NULL,
    sha256          VARCHAR NOT NULL,
    size_bytes      BIGINT  NOT NULL,
    http_status     INTEGER NOT NULL,
    content_type    VARCHAR
);

-- One row per ingestion attempt. Lineage and replayability live here.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id          VARCHAR PRIMARY KEY,
    job             VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    dataset         VARCHAR NOT NULL,
    business_date   DATE,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          VARCHAR NOT NULL,   -- ok | skipped | not_published | quarantined | failed
    rows_in         BIGINT,
    rows_out        BIGINT,
    rows_rejected   BIGINT DEFAULT 0,
    lake_key        VARCHAR,
    message         VARCHAR
);

-- Data-quality contract results. A failed check quarantines the payload.
CREATE TABLE IF NOT EXISTS dq_results (
    run_id          VARCHAR NOT NULL,
    check_name      VARCHAR NOT NULL,
    severity        VARCHAR NOT NULL,   -- error | warn
    passed          BOOLEAN NOT NULL,
    scope           VARCHAR DEFAULT 'file',  -- file | row
    observed        VARCHAR,
    detail          VARCHAR,
    checked_at      TIMESTAMPTZ NOT NULL
);

-- ------------------------------------------------------------ security master
-- C01. ISIN is the primary key: symbols change, exchange codes differ, companies
-- restructure. ISIN is the only stable join key in Indian markets.
CREATE TABLE IF NOT EXISTS securities (
    isin            VARCHAR PRIMARY KEY,
    first_seen      DATE NOT NULL,
    last_seen       DATE NOT NULL,
    instrument_type VARCHAR,            -- STK, GB, ETF, ...
    status          VARCHAR DEFAULT 'active'
);

-- Symbol history. One row per (isin, exchange, ticker, series) span actually observed
-- in the data - this is how a symbol change becomes a fact rather than a guess.
CREATE TABLE IF NOT EXISTS security_listings (
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR NOT NULL,
    ticker          VARCHAR NOT NULL,
    series          VARCHAR,
    instrument_id   VARCHAR,            -- BSE scrip code / NSE token
    first_seen      DATE NOT NULL,
    last_seen       DATE NOT NULL,
    PRIMARY KEY (isin, exchange, ticker, series)
);

CREATE TABLE IF NOT EXISTS security_names (
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR NOT NULL,
    name            VARCHAR NOT NULL,
    first_seen      DATE NOT NULL,
    last_seen       DATE NOT NULL,
    PRIMARY KEY (isin, exchange, name)
);

-- --------------------------------------------------------- corporate actions
-- Splits, bonuses, rights, dividends, mergers, symbol changes, delistings.
-- Adjustment factors are derived from these; prices are stored unadjusted.
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id       VARCHAR PRIMARY KEY,
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR,
    action_type     VARCHAR NOT NULL,   -- SPLIT | BONUS | RIGHTS | DIVIDEND | MERGER | SYMBOL_CHANGE | DELISTING
    ex_date         DATE NOT NULL,
    record_date     DATE,
    ratio_from      DOUBLE,             -- e.g. SPLIT 1 -> 5 : ratio_from=1, ratio_to=5
    ratio_to        DOUBLE,
    amount          DOUBLE,             -- dividend per share
    details         VARCHAR,
    source          VARCHAR NOT NULL,
    source_tier     INTEGER NOT NULL,
    published_at    TIMESTAMPTZ,
    observed_at     TIMESTAMPTZ NOT NULL,
    evidence_key    VARCHAR,            -- lake_manifest.key
    confidence      VARCHAR,            -- corroborated | single_exchange | reported
    derived_factor  DOUBLE              -- factor observed in the data, if derived
);

-- Derived: cumulative price/volume adjustment factor effective from a date.
CREATE TABLE IF NOT EXISTS adjustment_factors (
    isin            VARCHAR NOT NULL,
    effective_from  DATE NOT NULL,
    price_factor    DOUBLE NOT NULL,
    volume_factor   DOUBLE NOT NULL,
    derived_from    VARCHAR,
    computed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (isin, effective_from)
);

-- ---------------------------------------------------- point-in-time universe
-- C04. What was actually tradable on each date. Without this, backtests silently
-- survivor-bias: the failures are exactly the names absent from a current universe.
CREATE TABLE IF NOT EXISTS universe_snapshots (
    business_date   DATE NOT NULL,
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR NOT NULL,
    ticker          VARCHAR NOT NULL,
    series          VARCHAR,
    instrument_type VARCHAR,
    traded_volume   BIGINT,
    turnover        DOUBLE,
    trades          BIGINT,
    close_price     DOUBLE,
    tradable        BOOLEAN NOT NULL,
    PRIMARY KEY (business_date, isin, exchange, series)
);

-- Segment / surveillance flags that change tradability: T2T, ASM, GSM, suspensions.
CREATE TABLE IF NOT EXISTS security_flags (
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR,
    flag            VARCHAR NOT NULL,   -- T2T | ASM | GSM | SUSPENDED | FNO_ELIGIBLE
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    source          VARCHAR NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (isin, flag, valid_from)
);

-- ------------------------------------------------------- point-in-time facts
-- C03. THE component whose cost rises with every month of delay.
--
-- Append-only. Nothing in this table is ever updated or deleted. A restatement is a new
-- row with a later observed_at, so we can always reconstruct what was knowable on any
-- past date - which no Indian vendor sells affordably.
CREATE TABLE IF NOT EXISTS pit_observations (
    observation_id  VARCHAR PRIMARY KEY,
    entity_type     VARCHAR NOT NULL,   -- security | index | macro
    entity_key      VARCHAR NOT NULL,   -- ISIN, index name, series code
    attribute       VARCHAR NOT NULL,   -- revenue, eps, roce, repo_rate, ...
    period_start    DATE,
    period_end      DATE,
    value_num       DOUBLE,
    value_text      VARCHAR,
    unit            VARCHAR,
    -- the four timestamps (docs/ARCHITECTURE.md)
    event_time      TIMESTAMPTZ,        -- when it happened
    published_at    TIMESTAMPTZ,        -- when it became public  <- the PIT anchor
    observed_at     TIMESTAMPTZ NOT NULL,  -- when we saw it
    source          VARCHAR NOT NULL,
    source_tier     INTEGER NOT NULL,
    evidence_key    VARCHAR,            -- lake_manifest.key
    revision_of     VARCHAR,            -- prior observation_id if this restates it
    notes           VARCHAR
);

-- ----------------------------------------------------------- index benchmarks
CREATE TABLE IF NOT EXISTS index_levels (
    business_date   DATE NOT NULL,
    index_name      VARCHAR NOT NULL,
    open_level      DOUBLE,
    high_level      DOUBLE,
    low_level       DOUBLE,
    close_level     DOUBLE,
    variant         VARCHAR DEFAULT 'PRICE',  -- PRICE | TRI
    source          VARCHAR NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (business_date, index_name, variant)
);

-- Constituent history: not freely published in clean form, so we accumulate it.
CREATE TABLE IF NOT EXISTS index_constituents (
    index_name      VARCHAR NOT NULL,
    isin            VARCHAR NOT NULL,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    weight          DOUBLE,
    source          VARCHAR NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (index_name, isin, valid_from)
);

-- Rows rejected by a row-scope quality contract. Kept, not discarded: an exchange
-- publishing an impossible row is itself a finding, and we need the audit trail.
CREATE TABLE IF NOT EXISTS rejected_rows (
    run_id          VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    business_date   DATE NOT NULL,
    isin            VARCHAR,
    ticker          VARCHAR,
    reject_reason   VARCHAR NOT NULL,
    raw_row         VARCHAR,
    rejected_at     TIMESTAMPTZ NOT NULL
);

-- ------------------------------------------------------------ reference data
-- Static-ish security attributes from the exchange's own master list: listing date,
-- face value, market lot. Listing date matters because it bounds how far back a name
-- could possibly have been in any universe.
CREATE TABLE IF NOT EXISTS security_reference (
    isin            VARCHAR NOT NULL,
    exchange        VARCHAR NOT NULL,
    symbol          VARCHAR,
    company_name    VARCHAR,
    series          VARCHAR,
    listing_date    DATE,
    paid_up_value   DOUBLE,
    face_value      DOUBLE,
    market_lot      BIGINT,
    source          VARCHAR NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    evidence_key    VARCHAR,
    PRIMARY KEY (isin, exchange, series)
);

-- Triage log for large overnight gaps with no recorded corporate action.
-- A stock genuinely can move 40% overnight, so the standard is not "no gaps" but
-- "no gap left unexamined". Each entry records a decision, once, with a reason.
CREATE TABLE IF NOT EXISTS gap_reviews (
    isin            VARCHAR NOT NULL,
    ex_date         DATE NOT NULL,
    exchange        VARCHAR,
    observed_factor DOUBLE,
    verdict         VARCHAR NOT NULL,   -- price_move | action_recorded | needs_source
    note            VARCHAR,
    reviewed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (isin, ex_date)
);

-- ISIN successions: the same security continuing under a new ISIN.
-- In India a face-value split changes the ISIN, so without this table a company's
-- history breaks into two unrelated securities on every split. Detected from what stays
-- stable through the change - BSE's scrip code and NSE's ticker - on consecutive
-- sessions, and corroborated when both exchanges show the same (old, new) pair.
CREATE TABLE IF NOT EXISTS isin_successions (
    old_isin        VARCHAR NOT NULL,
    new_isin        VARCHAR NOT NULL,
    effective_date  DATE NOT NULL,      -- first session under the new ISIN (earliest exchange)
    exchanges       VARCHAR NOT NULL,   -- BSE | NSE | BSE+NSE
    price_ratio     DOUBLE,             -- first new close / last old close (median over exchanges)
    confidence      VARCHAR NOT NULL,   -- corroborated | single_exchange
    evidence        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (old_isin, new_isin)
);

-- Corporate announcements (Tier 1, BSE), classified deterministically.
-- Timestamps are IST wall-clock as published. A decision may use an announcement only
-- from published_at onwards (ARCHITECTURE.md §5.2); observed_at is when we saw it.
CREATE TABLE IF NOT EXISTS announcements (
    news_id         VARCHAR PRIMARY KEY,
    source          VARCHAR NOT NULL,
    business_date   DATE NOT NULL,       -- the day queried
    scrip_code      VARCHAR,
    isin            VARCHAR,             -- resolved as of business_date; NULL if unlisted
    company         VARCHAR,
    category        VARCHAR,
    subcategory     VARCHAR,
    headline        VARCHAR,
    subject         VARCHAR,
    event_type      VARCHAR NOT NULL,
    materiality     VARCHAR NOT NULL,    -- high | medium | low
    rule            VARCHAR,             -- which classification rule fired
    critical        BOOLEAN,
    submitted_at    TIMESTAMP,
    published_at    TIMESTAMP,
    news_at         TIMESTAMP,
    attachment      VARCHAR,
    evidence_key    VARCHAR NOT NULL,    -- lake_manifest.key
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Index renames, so each index is one continuous series (see indices/lineage.py).
CREATE TABLE IF NOT EXISTS index_aliases (
    old_name        VARCHAR PRIMARY KEY,
    new_name        VARCHAR NOT NULL,
    effective_date  DATE NOT NULL,        -- first session under the new name
    ratio           DOUBLE,               -- new open / old close across the rename
    family_median   DOUBLE,               -- median ratio of all renames that day
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Market regime per session (regime/brain.py). Versioned: a rule change is a new
-- version, never a silent rewrite of history.
CREATE TABLE IF NOT EXISTS market_regime (
    business_date   DATE NOT NULL,
    version         VARCHAR NOT NULL,
    regime          VARCHAR NOT NULL,   -- after hysteresis: CRISIS | RISK_OFF | NEUTRAL | RISK_ON
    raw_regime      VARCHAR NOT NULL,   -- what this session alone indicated
    reasons         VARCHAR,
    nifty_close     DOUBLE, ma50 DOUBLE, ma200 DOUBLE, drawdown DOUBLE, ret20 DOUBLE,
    rv20            DOUBLE, vix DOUBLE, breadth_200 DOUBLE, breadth_50 DOUBLE,
    advances        BIGINT, declines BIGINT,
    computed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (business_date, version)
);

-- Evidence ledger (evidence/ledger.py). Immutable: corrections are new rows that
-- supersede old ones; nothing is ever updated.
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     VARCHAR PRIMARY KEY,   -- hash of kind, subject, as_of, value, derivation
    kind            VARCHAR NOT NULL,      -- index_close | regime | announcement | price_move | ...
    subject         VARCHAR NOT NULL,      -- ISIN, index name, or MARKET
    as_of           TIMESTAMP NOT NULL,    -- event time the claim is true at
    published_at    TIMESTAMP,             -- when it became public (point-in-time use)
    claim           VARCHAR NOT NULL,      -- the human-readable statement
    value           VARCHAR,               -- JSON
    source          VARCHAR NOT NULL,
    source_tier     INTEGER NOT NULL,
    lake_key        VARCHAR,               -- lake_manifest.key: the bytes it came from
    derivation      VARCHAR NOT NULL,      -- rule / version that produced it
    confidence      VARCHAR NOT NULL,
    quality         VARCHAR NOT NULL,
    supersedes      VARCHAR,               -- evidence_id this corrects
    inputs          VARCHAR,               -- JSON list of evidence_ids a derived claim used
    observed_at     TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_use (
    evidence_id     VARCHAR NOT NULL,
    used_by_kind    VARCHAR NOT NULL,      -- world_state | brief | decision
    used_by_id      VARCHAR NOT NULL,
    used_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (evidence_id, used_by_kind, used_by_id)
);

-- World state (worldstate/build.py): one immutable, content-addressed snapshot per
-- session. Every item inside cites evidence_ids.
CREATE TABLE IF NOT EXISTS world_states (
    version_id      VARCHAR PRIMARY KEY,   -- hash of content
    business_date   DATE NOT NULL,
    built_at        TIMESTAMPTZ NOT NULL,
    content         VARCHAR NOT NULL,      -- JSON
    evidence_count  INTEGER NOT NULL,
    builder         VARCHAR NOT NULL       -- builder version
);

-- Decisions (decisions/record.py). Content is frozen; the lifecycle is an append-only
-- event log. Nothing here is ever updated or deleted.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id         VARCHAR PRIMARY KEY,   -- hash of content
    isin                VARCHAR NOT NULL,
    action              VARCHAR NOT NULL,
    horizon_days        INTEGER NOT NULL,
    world_state_version VARCHAR NOT NULL,
    content             VARCHAR NOT NULL,      -- JSON: thesis, evidence, uncertainty, ...
    author              VARCHAR NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_events (
    decision_id     VARCHAR NOT NULL,
    seq             INTEGER NOT NULL,
    from_state      VARCHAR,
    to_state        VARCHAR NOT NULL,
    actor           VARCHAR NOT NULL,
    note            VARCHAR,
    event_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (decision_id, seq)
);

-- P2-4 knowledge graph: filers named in SAST / insider disclosures (graph/build.py).
CREATE TABLE IF NOT EXISTS holder_filings (
    news_id         VARCHAR PRIMARY KEY,   -- announcements.news_id: the filing itself
    business_date   DATE NOT NULL,
    published_at    TIMESTAMP,
    scrip_code      VARCHAR,
    isin            VARCHAR,
    company         VARCHAR,
    relation        VARCHAR NOT NULL,      -- PLEDGE | EXEMPT | OPEN_OFFER | SUBSTANTIAL | INSIDER
    filer           VARCHAR NOT NULL,
    filer_key       VARCHAR NOT NULL,      -- normalised identity
    filer_kind      VARCHAR NOT NULL       -- person | organisation | institution
);

CREATE TABLE IF NOT EXISTS promoter_groups (
    group_id        VARCHAR NOT NULL,
    anchor          VARCHAR,               -- the group's most active promoter organisation
    member          VARCHAR NOT NULL,      -- ISIN, or BSE:<scrip> if unresolved
    company         VARCHAR,
    size            INTEGER NOT NULL,
    PRIMARY KEY (group_id, member)
);

-- P2-5 every language-model call, gated and recorded (llm/gate.py). Prompts are hashed.
CREATE TABLE IF NOT EXISTS llm_calls (
    called_at       TIMESTAMPTZ NOT NULL,
    purpose         VARCHAR NOT NULL,
    model           VARCHAR NOT NULL,
    prompt_sha256   VARCHAR NOT NULL,
    output_sha256   VARCHAR NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    evidence        VARCHAR[]
);

-- Every validation run is a trial; the firewall deflates by all of them (firewall.py).
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_at          TIMESTAMPTZ NOT NULL,
    version         VARCHAR NOT NULL,
    feature         VARCHAR NOT NULL,
    horizon         INTEGER NOT NULL,
    params          VARCHAR,
    dates           INTEGER,
    mean_ic         DOUBLE,
    ic_t            DOUBLE,
    sharpe          DOUBLE,
    deflated_sharpe DOUBLE,
    verdict         VARCHAR NOT NULL,
    reasons         VARCHAR[],
    -- The per-rebalance IC series, and the dates it was measured on.
    --
    -- Stored because the Bonferroni bar over every trial assumes the trials are INDEPENDENT, and
    -- seventy-six of the first hundred and fifty sat on five features testing three ideas. The
    -- effective number of independent tests is estimable from the CORRELATION between trials' IC
    -- series - the same eigenvalue statistic risk/decompose.effective_bets uses for a portfolio -
    -- and without the series that correlation cannot be computed, so the effective count could
    -- only be bounded to [families, trials] = [7, 150]. An interval that wide cannot decide
    -- anything. See evaluation/families.py.
    ic_series       DOUBLE[],
    ic_dates        DATE[]
);

-- Pre-registered hypotheses and every test of them (evaluation/registry.py).
CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id   VARCHAR PRIMARY KEY,   -- hash of the spec (name excluded)
    name            VARCHAR NOT NULL,
    spec            VARCHAR NOT NULL,
    data_cutoff     DATE,                  -- last feature date when registered
    registered_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS hypothesis_tests (
    hypothesis_id   VARCHAR NOT NULL,
    mode            VARCHAR NOT NULL,      -- in_sample | out_of_sample
    tested_at       TIMESTAMPTZ NOT NULL,
    verdict         VARCHAR NOT NULL,
    result          VARCHAR NOT NULL
);

-- Paper trades for decisions at PAPER_CANDIDATE (paper/ledger.py).
CREATE TABLE IF NOT EXISTS paper_trades (
    decision_id     VARCHAR PRIMARY KEY,
    isin            VARCHAR NOT NULL,
    lineage         VARCHAR NOT NULL,
    action          VARCHAR NOT NULL,
    direction       INTEGER NOT NULL,      -- +1 long, -1 avoid/exit, 0 watch
    weight          DOUBLE,
    entry_date      DATE NOT NULL,
    entry_price     DOUBLE NOT NULL,       -- adjusted close
    entry_nifty     DOUBLE,
    due_date        DATE NOT NULL,
    cost            DOUBLE NOT NULL,       -- round trip, fraction
    bucket          VARCHAR,
    status          VARCHAR NOT NULL,      -- open | closed
    exit_date       DATE,
    exit_price      DOUBLE,
    stock_return    DOUBLE,
    nifty_return    DOUBLE,
    excess          DOUBLE,                -- direction x (stock - nifty) - cost
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ
);

-- Model benchmark: every (task, model) measurement, append-only (evaluation/models.py).
CREATE TABLE IF NOT EXISTS model_bench (
    run_at          TIMESTAMPTZ NOT NULL,
    task            VARCHAR NOT NULL,
    model           VARCHAR NOT NULL,
    n               INTEGER NOT NULL,
    accuracy        DOUBLE,
    macro_f1        DOUBLE,
    latency_ms      DOUBLE,
    target          DOUBLE,
    threshold       DOUBLE,              -- NULL: confidence never reached the target
    coverage        DOUBLE,
    detail          VARCHAR
);

-- Shareholder tone per announcement, via the model router (events/tone.py).
CREATE TABLE IF NOT EXISTS announcement_tone (
    news_id         VARCHAR PRIMARY KEY,
    tone            VARCHAR NOT NULL,      -- positive | negative | neutral
    confidence      DOUBLE,
    model           VARCHAR NOT NULL,
    accepted        BOOLEAN NOT NULL,      -- cleared that model's calibrated bar
    route           VARCHAR,
    text_source     VARCHAR,               -- filing | news_headline: what was classified
    classified_at   TIMESTAMPTZ NOT NULL
);

-- Investment committee runs (committee/run.py): stances, cited debate, drafted decision.
CREATE TABLE IF NOT EXISTS committee_runs (
    isin                VARCHAR NOT NULL,
    world_state_version VARCHAR NOT NULL,
    model               VARCHAR NOT NULL,
    result              VARCHAR NOT NULL,
    decision_id         VARCHAR,
    run_at              TIMESTAMPTZ NOT NULL
);

-- The chosen route per task (evaluation/models.optimise_route); newest wins.
CREATE TABLE IF NOT EXISTS model_routes (
    task            VARCHAR NOT NULL,
    steps           VARCHAR NOT NULL,      -- JSON route steps incl. per-label thresholds
    simulated       VARCHAR,               -- accuracy / uncertain / latency when chosen
    budget_ms       DOUBLE,
    chosen_at       TIMESTAMPTZ NOT NULL
);

-- News a filing refers to, recovered from the filing's own text (events/newsref.py).
-- Deterministic: no request is made to the publisher.
CREATE TABLE IF NOT EXISTS announcement_news (
    news_id         VARCHAR PRIMARY KEY,
    url             VARCHAR,
    domain          VARCHAR,
    headline        VARCHAR,               -- NULL when the filing carries only a link
    how             VARCHAR NOT NULL,      -- quoted | url_slug | link_only
    extracted_at    TIMESTAMPTZ NOT NULL
);

-- Daily mutual-fund NAVs from AMFI's public feed (providers/amfi.py). Keyed by the date
-- the row carries, not the date we fetched.
CREATE TABLE IF NOT EXISTS mf_nav (
    scheme_code     VARCHAR NOT NULL,
    isin_growth     VARCHAR,
    isin_reinvest   VARCHAR,
    scheme_name     VARCHAR NOT NULL,
    fund_house      VARCHAR,
    scheme_type     VARCHAR,
    plan            VARCHAR,
    option          VARCHAR,
    nav             DOUBLE NOT NULL,
    nav_date        DATE NOT NULL,
    lake_key        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scheme_code, nav_date)
);

-- Append-only observations of AMFI NAV rows. A source may republish a corrected NAV for
-- the same scheme/date; keep every byte-backed observation and use observed_at as the
-- conservative availability bound. AMFI's daily file does not provide a per-row
-- publication timestamp, so source_published_at remains NULL unless an authorized
-- provider explicitly supplies one.
CREATE TABLE IF NOT EXISTS mf_nav_observations (
    observation_id      VARCHAR PRIMARY KEY,
    scheme_code         VARCHAR NOT NULL,
    isin_growth         VARCHAR,
    isin_reinvest       VARCHAR,
    scheme_name         VARCHAR NOT NULL,
    fund_house          VARCHAR,
    scheme_type         VARCHAR,
    plan                VARCHAR,
    option              VARCHAR,
    nav                 DOUBLE NOT NULL,
    nav_date            DATE NOT NULL,
    lake_key            VARCHAR NOT NULL,
    row_number          INTEGER NOT NULL,
    row_sha256          VARCHAR NOT NULL,
    source_published_at TIMESTAMPTZ,
    observed_at         TIMESTAMPTZ NOT NULL,
    ingested_at         TIMESTAMPTZ
);

-- Effective-dated mutual-fund reference metadata. Each immutable row carries its source
-- hash and both publication and observation clocks; unavailable classifications are
-- explicit UNKNOWN values rather than guessed from a scheme name.
CREATE TABLE IF NOT EXISTS mf_scheme_master (
    record_id       VARCHAR PRIMARY KEY,
    scheme_code     VARCHAR NOT NULL,
    plan            VARCHAR NOT NULL,
    option          VARCHAR NOT NULL,
    isin            VARCHAR NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    structure       VARCHAR NOT NULL,
    investment_style VARCHAR NOT NULL,
    asset_category  VARCHAR NOT NULL,
    category_label  VARCHAR NOT NULL,
    fund_house      VARCHAR,
    riskometer      VARCHAR,
    benchmark_tri   VARCHAR,
    manager         VARCHAR,
    objective       VARCHAR,
    source_id       VARCHAR NOT NULL,
    source_ref      VARCHAR NOT NULL,
    source_sha256   VARCHAR NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Append-only, permission-gated lifecycle facts keep closed, merged, renamed, and
-- suspended schemes in point-in-time universes. Successor links are explicit evidence;
-- they are never inferred from similar names or current scheme availability.
CREATE TABLE IF NOT EXISTS mf_scheme_lifecycle (
    record_id       VARCHAR PRIMARY KEY,
    scheme_code     VARCHAR NOT NULL,
    plan            VARCHAR NOT NULL,
    option          VARCHAR NOT NULL,
    isin            VARCHAR NOT NULL,
    lifecycle       VARCHAR NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    successor_scheme_code VARCHAR,
    source_id       VARCHAR NOT NULL,
    source_ref       VARCHAR NOT NULL,
    source_sha256    VARCHAR NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL
);

-- Disclosed, effective-dated fees and dealing constraints. Numeric amounts/rates are
-- stored as text so source decimal precision survives round trips; JSON null means
-- unknown while [] means a source explicitly reported no schedule/restriction.
CREATE TABLE IF NOT EXISTS mf_scheme_commercial_terms (
    record_id       VARCHAR PRIMARY KEY,
    scheme_code     VARCHAR NOT NULL,
    plan            VARCHAR NOT NULL,
    option          VARCHAR NOT NULL,
    isin            VARCHAR NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    ter_percent     VARCHAR,
    aum_inr         VARCHAR,
    exit_loads_json VARCHAR,
    charge_schedule_json VARCHAR,
    minimum_initial_inr VARCHAR,
    minimum_additional_inr VARCHAR,
    minimum_redemption_inr VARCHAR,
    exit_restrictions_json VARCHAR,
    source_id       VARCHAR NOT NULL,
    source_ref       VARCHAR NOT NULL,
    source_sha256    VARCHAR NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL
);

-- Permission-gated, append-only IDCW and unit/scheme transition facts for MF paper
-- accounting and total-return reconstruction. All per-unit amounts and ratios retain
-- source decimal precision as text.
CREATE TABLE IF NOT EXISTS mf_scheme_flows (
    event_id        VARCHAR PRIMARY KEY,
    scheme_code     VARCHAR NOT NULL,
    isin            VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL,
    effective_date  DATE NOT NULL,
    cash_per_unit   VARCHAR,
    reinvestment_nav VARCHAR,
    unit_ratio      VARCHAR,
    successor_scheme_code VARCHAR,
    successor_isin   VARCHAR,
    source_id       VARCHAR NOT NULL,
    source_ref       VARCHAR NOT NULL,
    source_sha256    VARCHAR NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL
);

-- Source-hashed monthly/quarterly portfolio rows, available only after both
-- publication and observation clocks. Decimal weights and debt metrics retain their
-- exact source representation; the same disclosure date can have later corrections.
CREATE TABLE IF NOT EXISTS mf_portfolio_disclosures (
    record_id       VARCHAR PRIMARY KEY,
    scheme_code     VARCHAR NOT NULL,
    plan            VARCHAR NOT NULL,
    option          VARCHAR NOT NULL,
    scheme_isin     VARCHAR NOT NULL,
    disclosure_date DATE NOT NULL,
    asset_class     VARCHAR NOT NULL,
    weight_percent  VARCHAR NOT NULL,
    asset_id        VARCHAR,
    asset_name      VARCHAR,
    sector          VARCHAR,
    credit_rating   VARCHAR,
    modified_duration_years VARCHAR,
    yield_to_maturity_percent VARCHAR,
    source_id       VARCHAR NOT NULL,
    source_ref       VARCHAR NOT NULL,
    source_sha256    VARCHAR NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL
);

-- Durable paper mutual-fund accounting. Exit-load terms are frozen per purchase lot
-- so a later schedule revision cannot rewrite an existing lot's redemption economics.
CREATE TABLE IF NOT EXISTS mf_paper_accounts (
    account_id VARCHAR PRIMARY KEY,
    cash_inr DECIMAL(38, 18) NOT NULL CHECK (cash_inr >= 0),
    created_at TIMESTAMPTZ NOT NULL
);

-- Balanced paper MF cash/investment journal. Legacy balances are not backfilled:
-- their opening source is unknown and must be reconciled explicitly.
CREATE TABLE IF NOT EXISTS mf_paper_ledger_entries (
    account_id VARCHAR NOT NULL,
    transaction_id VARCHAR NOT NULL,
    line_no INTEGER NOT NULL CHECK (line_no > 0),
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    account_code VARCHAR NOT NULL CHECK (account_code IN (
        'CASH', 'MF_INVESTMENTS', 'REDEMPTION_RECEIVABLE',
        'OWNER_EQUITY', 'OWNER_WITHDRAWALS', 'EXIT_LOAD_EXPENSE',
        'OTHER_CHARGES_EXPENSE', 'IDCW_INCOME', 'REALIZED_GAIN', 'REALIZED_LOSS',
        'SIP_PAYMENT_PENDING'
    )),
    debit_inr DECIMAL(38, 18) NOT NULL CHECK (debit_inr >= 0),
    credit_inr DECIMAL(38, 18) NOT NULL CHECK (credit_inr >= 0),
    basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
    source_id VARCHAR,
    source_ref VARCHAR,
    source_sha256 VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (account_id, transaction_id, line_no),
    CHECK ((debit_inr > 0 AND credit_inr = 0) OR (credit_inr > 0 AND debit_inr = 0)),
    CHECK ((basis='OBSERVED_EVIDENCE' AND source_id IS NOT NULL
            AND source_ref IS NOT NULL AND source_sha256 IS NOT NULL)
        OR (basis='SIMULATED_ASSUMPTION' AND source_id IS NULL
            AND source_ref IS NULL AND source_sha256 IS NULL))
);

CREATE TABLE IF NOT EXISTS mf_paper_external_cash_events (
    account_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    amount_inr DECIMAL(38, 18) NOT NULL CHECK (amount_inr > 0),
    direction VARCHAR NOT NULL CHECK (direction IN ('CONTRIBUTION', 'WITHDRAWAL')),
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
    source_id VARCHAR,
    source_ref VARCHAR,
    source_sha256 VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (account_id, event_id),
    CHECK ((basis='OBSERVED_EVIDENCE' AND source_id IS NOT NULL
            AND source_ref IS NOT NULL AND source_sha256 IS NOT NULL)
        OR (basis='SIMULATED_ASSUMPTION' AND source_id IS NULL
            AND source_ref IS NULL AND source_sha256 IS NULL))
);

-- Immutable MF requests and append-only platform/simulation lifecycle receipts.
CREATE TABLE IF NOT EXISTS mf_paper_orders (
    order_id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    transaction_type VARCHAR NOT NULL CHECK (transaction_type IN (
        'PURCHASE', 'REDEMPTION', 'SWITCH_IN', 'SWITCH_OUT'
    )),
    requested_at TIMESTAMPTZ NOT NULL,
    amount_inr DECIMAL(38, 18),
    units DECIMAL(38, 18),
    request_basis VARCHAR NOT NULL CHECK (request_basis IN (
        'SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE'
    )),
    request_sha256 VARCHAR NOT NULL,
    valid_through DATE NOT NULL,
    current_state VARCHAR NOT NULL CHECK (current_state IN (
        'REQUESTED', 'ACKNOWLEDGED', 'ACCEPTED', 'REJECTED'
    )),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK ((amount_inr IS NOT NULL AND amount_inr > 0 AND units IS NULL)
        OR (units IS NOT NULL AND units > 0 AND amount_inr IS NULL))
);

CREATE TABLE IF NOT EXISTS mf_paper_order_transitions (
    order_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    state VARCHAR NOT NULL CHECK (state IN (
        'REQUESTED', 'ACKNOWLEDGED', 'ACCEPTED', 'REJECTED'
    )),
    effective_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
    source_id VARCHAR,
    source_ref VARCHAR,
    source_sha256 VARCHAR,
    reason VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (order_id, sequence),
    UNIQUE (order_id, event_sha256)
);

-- Ordinary MF purchase payment milestones and derived, immutable eligible-NAV state.
CREATE TABLE IF NOT EXISTS mf_paper_purchase_payment_events (
    order_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    milestone_kind VARCHAR NOT NULL CHECK (milestone_kind IN (
        'INITIATED', 'CREDITED_TO_SCHEME', 'AVAILABLE_FOR_UTILIZATION'
    )),
    effective_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
    source_id VARCHAR,
    source_ref VARCHAR,
    source_sha256 VARCHAR,
    permission_evidence_json VARCHAR NOT NULL DEFAULT '[]',
    event_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (order_id, sequence),
    UNIQUE (order_id, milestone_kind),
    UNIQUE (order_id, event_sha256),
    CHECK (received_at >= effective_at),
    CHECK ((basis='OBSERVED_EVIDENCE' AND source_id IS NOT NULL
        AND source_ref IS NOT NULL AND source_sha256 IS NOT NULL)
        OR (basis='SIMULATED_ASSUMPTION' AND source_id IS NULL
        AND source_ref IS NULL AND source_sha256 IS NULL))
);

CREATE TABLE IF NOT EXISTS mf_paper_purchase_eligibility (
    order_id VARCHAR PRIMARY KEY,
    state VARCHAR NOT NULL CHECK (state IN ('NAV_PENDING', 'ALLOTTED', 'CANCELLED')),
    eligible_nav_date DATE NOT NULL,
    transaction_at TIMESTAMPTZ NOT NULL,
    decision_at TIMESTAMPTZ NOT NULL,
    evidence_json VARCHAR NOT NULL,
    eligibility_sha256 VARCHAR NOT NULL,
    nav_observation_id VARCHAR,
    nav_per_unit DECIMAL(38, 18),
    units_allotted DECIMAL(38, 18),
    allotment_sha256 VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK ((state='NAV_PENDING' AND nav_observation_id IS NULL
        AND nav_per_unit IS NULL AND units_allotted IS NULL AND allotment_sha256 IS NULL)
        OR (state='ALLOTTED' AND nav_observation_id IS NOT NULL
        AND nav_per_unit > 0 AND units_allotted > 0 AND allotment_sha256 IS NOT NULL)
        OR (state='CANCELLED' AND nav_observation_id IS NULL
        AND nav_per_unit IS NULL AND units_allotted IS NULL AND allotment_sha256 IS NULL))
);

-- Scheduled MF SIP debits are independent installments with terminal missed outcomes.
CREATE TABLE IF NOT EXISTS mf_paper_sip_plans (
    plan_id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    plan_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_paper_sip_installments (
    installment_id VARCHAR PRIMARY KEY,
    plan_id VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    scheduled_for DATE NOT NULL,
    amount_inr DECIMAL(38, 18) NOT NULL CHECK (amount_inr > 0),
    current_state VARCHAR NOT NULL CHECK (current_state IN (
        'SCHEDULED', 'PAYMENT_INITIATED', 'FUNDS_AVAILABLE', 'NAV_PENDING', 'MISSED', 'ALLOTTED'
    )),
    payment_initiated_at TIMESTAMPTZ,
    funds_available_at TIMESTAMPTZ,
    missed_at TIMESTAMPTZ,
    miss_reason VARCHAR,
    nav_date DATE,
    nav_per_unit DECIMAL(38, 18),
    nav_published_at TIMESTAMPTZ,
    nav_available_at TIMESTAMPTZ,
    allotted_at TIMESTAMPTZ,
    units_allotted DECIMAL(38, 18),
    state_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (plan_id, scheduled_for)
);

CREATE TABLE IF NOT EXISTS mf_paper_sip_transitions (
    installment_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    from_state VARCHAR,
    to_state VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (installment_id, sequence),
    UNIQUE (installment_id, event_sha256)
);

-- Durable SWP/STP instructions; events record settlement and any target purchase
-- independently so unsettled redemption proceeds are never made spendable early.
CREATE TABLE IF NOT EXISTS mf_paper_systematic_plans (
    plan_id VARCHAR PRIMARY KEY,
    account_id VARCHAR NOT NULL,
    plan_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_paper_systematic_instructions (
    instruction_id VARCHAR PRIMARY KEY,
    plan_id VARCHAR NOT NULL,
    account_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN ('SWP', 'STP')),
    scheduled_for DATE NOT NULL,
    source_scheme VARCHAR NOT NULL,
    source_plan VARCHAR NOT NULL,
    source_option VARCHAR NOT NULL,
    source_isin VARCHAR NOT NULL,
    amount_inr DECIMAL(38, 18),
    units DECIMAL(38, 18),
    target_scheme VARCHAR,
    target_plan VARCHAR,
    target_option VARCHAR,
    target_isin VARCHAR,
    current_stage VARCHAR NOT NULL,
    state_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (plan_id, scheduled_for),
    CHECK ((amount_inr IS NOT NULL AND amount_inr > 0 AND units IS NULL)
        OR (units IS NOT NULL AND units > 0 AND amount_inr IS NULL))
);

CREATE TABLE IF NOT EXISTS mf_paper_systematic_events (
    instruction_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL CHECK (kind IN (
        'REDEMPTION_REQUESTED', 'VALUE_CONFIRMED', 'PAYOUT_AVAILABLE',
        'PURCHASE_FUNDS_APPLIED', 'ALLOTMENT_CONFIRMED', 'PURCHASE_CANCELLED', 'MISSED'
    )),
    effective_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    basis VARCHAR NOT NULL CHECK (basis IN ('SIMULATED_ASSUMPTION', 'OBSERVED_EVIDENCE')),
    amount_inr DECIMAL(38, 18),
    gross_inr DECIMAL(38, 18),
    exit_load_inr DECIMAL(38, 18),
    other_charges_inr DECIMAL(38, 18),
    nav_per_unit DECIMAL(38, 18),
    units DECIMAL(38, 18),
    reason VARCHAR,
    source_id VARCHAR,
    source_ref VARCHAR,
    source_sha256 VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (instruction_id, sequence),
    UNIQUE (instruction_id, event_id),
    UNIQUE (instruction_id, event_sha256)
);

-- Fixed-INR SWP/STP requests retain accepted-order NAV eligibility until the
-- eligible NAV is published and the resulting units can be reserved precisely.
CREATE TABLE IF NOT EXISTS mf_paper_systematic_redemption_eligibility (
    instruction_id VARCHAR PRIMARY KEY,
    order_id VARCHAR NOT NULL UNIQUE,
    requested_at TIMESTAMPTZ NOT NULL,
    eligible_nav_date DATE NOT NULL,
    transaction_at TIMESTAMPTZ NOT NULL,
    decision_at TIMESTAMPTZ NOT NULL,
    evidence_json VARCHAR NOT NULL,
    eligibility_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_paper_lots (
    account_id VARCHAR NOT NULL,
    lot_id VARCHAR NOT NULL,
    folio_id VARCHAR,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    isin VARCHAR NOT NULL,
    allotted_on DATE NOT NULL,
    lock_in_until DATE,
    units_original DECIMAL(38, 18) NOT NULL CHECK (units_original > 0),
    units_remaining DECIMAL(38, 18) NOT NULL CHECK (units_remaining >= 0),
    purchase_nav DECIMAL(38, 18) NOT NULL CHECK (purchase_nav > 0),
    exit_loads_json VARCHAR,
    exit_loads_sha256 VARCHAR,
    subscription_event_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, lot_id),
    UNIQUE (account_id, subscription_event_id)
);

-- Legacy paper lots keep folio_id NULL until an operator records supporting
-- ownership evidence. Reconciliation is append-only at the API layer.
CREATE TABLE IF NOT EXISTS mf_paper_folio_reconciliations (
    account_id VARCHAR NOT NULL,
    lot_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    evidence_ref VARCHAR NOT NULL,
    evidence_sha256 VARCHAR NOT NULL,
    reconciled_by VARCHAR NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    event_sha256 VARCHAR NOT NULL,
    PRIMARY KEY (account_id, lot_id)
);

CREATE TABLE IF NOT EXISTS mf_paper_events (
    account_id VARCHAR NOT NULL,
    event_id VARCHAR NOT NULL,
    folio_id VARCHAR,
    kind VARCHAR NOT NULL CHECK (kind IN ('SUBSCRIBE', 'REDEEM')),
    scheme_code VARCHAR,
    plan VARCHAR,
    option VARCHAR,
    isin VARCHAR,
    event_date DATE,
    nav_per_unit DECIMAL(38, 18),
    units DECIMAL(38, 18),
    gross_inr DECIMAL(38, 18),
    exit_load_inr DECIMAL(38, 18),
    other_charges_inr DECIMAL(38, 18) DEFAULT 0,
    net_inr DECIMAL(38, 18),
    event_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, event_id)
);

-- MF redemption requests reserve units at submission, realize value only after the
-- eligible NAV is known, and keep proceeds unavailable until the payout transition.
CREATE TABLE IF NOT EXISTS mf_paper_redemption_settlements (
    account_id VARCHAR NOT NULL,
    settlement_id VARCHAR NOT NULL,
    folio_id VARCHAR,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    isin VARCHAR NOT NULL,
    requested_units DECIMAL(38, 18) NOT NULL CHECK (requested_units > 0),
    requested_on DATE NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    state VARCHAR NOT NULL CHECK (state IN (
        'REDEMPTION_PENDING', 'VALUE_CONFIRMED', 'PAYOUT_PENDING', 'CASH_AVAILABLE'
    )),
    redemption_event_id VARCHAR,
    eligible_nav_date DATE,
    order_id VARCHAR,
    eligibility_sha256 VARCHAR,
    eligibility_evidence_json VARCHAR,
    nav_observation_id VARCHAR,
    nav_per_unit DECIMAL(38, 18),
    gross_inr DECIMAL(38, 18),
    exit_load_inr DECIMAL(38, 18),
    other_charges_inr DECIMAL(38, 18) DEFAULT 0,
    net_inr DECIMAL(38, 18),
    payout_due_date DATE,
    payout_available_at TIMESTAMPTZ,
    payout_basis VARCHAR,
    payout_evidence_ref VARCHAR,
    payout_source_id VARCHAR,
    payout_source_sha256 VARCHAR,
    request_sha256 VARCHAR NOT NULL,
    value_sha256 VARCHAR,
    payout_sha256 VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, settlement_id),
    UNIQUE (account_id, redemption_event_id)
);

CREATE TABLE IF NOT EXISTS mf_paper_redemption_transitions (
    account_id VARCHAR NOT NULL,
    settlement_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    state VARCHAR NOT NULL CHECK (state IN (
        'REDEMPTION_PENDING', 'VALUE_CONFIRMED', 'PAYOUT_PENDING', 'CASH_AVAILABLE'
    )),
    transitioned_at TIMESTAMPTZ NOT NULL,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (account_id, settlement_id, sequence),
    UNIQUE (account_id, settlement_id, state)
);

-- A switch is a durable link between the source redemption settlement and the target
-- switch-in purchase. Proceeds are not eligible to fund the target leg until the
-- source settlement is CASH_AVAILABLE at a strictly earlier timestamp.
CREATE TABLE IF NOT EXISTS mf_paper_switches (
    account_id VARCHAR NOT NULL,
    switch_id VARCHAR NOT NULL,
    folio_id VARCHAR NOT NULL,
    source_scheme VARCHAR NOT NULL,
    source_plan VARCHAR NOT NULL,
    source_option VARCHAR NOT NULL,
    source_isin VARCHAR NOT NULL,
    target_scheme VARCHAR NOT NULL,
    target_plan VARCHAR NOT NULL,
    target_option VARCHAR NOT NULL,
    target_isin VARCHAR NOT NULL,
    requested_units DECIMAL(38,18) NOT NULL CHECK (requested_units > 0),
    requested_at TIMESTAMPTZ NOT NULL,
    settlement_id VARCHAR NOT NULL,
    source_order_id VARCHAR,
    target_order_id VARCHAR,
    state VARCHAR NOT NULL CHECK (state IN (
        'REQUESTED', 'SWITCH_OUT_VALUE_CONFIRMED', 'SWITCH_OUT_PROCEEDS_AVAILABLE',
        'SWITCH_IN_FUNDS_APPLIED', 'SWITCH_IN_ALLOTMENT_CONFIRMED'
    )),
    source_net_inr DECIMAL(38,18),
    proceeds_available_at TIMESTAMPTZ,
    funds_applied_inr DECIMAL(38,18),
    target_units DECIMAL(38,18),
    target_nav DECIMAL(38,18),
    request_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, switch_id),
    UNIQUE (account_id, settlement_id)
);

CREATE TABLE IF NOT EXISTS mf_paper_switch_transitions (
    account_id VARCHAR NOT NULL,
    switch_id VARCHAR NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    state VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    amount_inr DECIMAL(38,18),
    related_id VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    evidence_json VARCHAR NOT NULL,
    PRIMARY KEY (account_id, switch_id, sequence),
    UNIQUE (account_id, switch_id, state),
    UNIQUE (account_id, switch_id, event_sha256)
);

-- Durable paper-book application of already-attested, point-in-time MF economic
-- flows. The source event remains in mf_scheme_flows; this account-scoped journal
-- makes unit/cash adjustments idempotent and auditable across restart.
CREATE TABLE IF NOT EXISTS mf_paper_flow_events (
    account_id VARCHAR NOT NULL,
    source_flow_id VARCHAR NOT NULL,
    flow_kind VARCHAR NOT NULL,
    scheme_code VARCHAR NOT NULL,
    plan VARCHAR NOT NULL,
    option VARCHAR NOT NULL,
    isin VARCHAR NOT NULL,
    target_scheme_code VARCHAR NOT NULL,
    target_plan VARCHAR NOT NULL,
    target_option VARCHAR NOT NULL,
    target_isin VARCHAR NOT NULL,
    effective_date DATE NOT NULL,
    units_before DECIMAL(38, 18) NOT NULL,
    units_after DECIMAL(38, 18) NOT NULL,
    cash_delta_inr DECIMAL(38, 18) NOT NULL,
    nav_before DECIMAL(38, 18) NOT NULL,
    nav_after DECIMAL(38, 18) NOT NULL,
    value_before_inr DECIMAL(38, 18) NOT NULL,
    value_after_inr DECIMAL(38, 18) NOT NULL,
    total_return_percent DECIMAL(38, 18) NOT NULL,
    reinvestment_lot_id VARCHAR,
    terms_record_id VARCHAR,
    event_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, source_flow_id)
);

-- NSE security-wise delivery position (providers/nse_delivery.py): the share of each day's traded quantity that
-- went to delivery. Filed under the trade date the file's header states.
CREATE TABLE IF NOT EXISTS delivery (
    symbol          VARCHAR NOT NULL,
    series          VARCHAR NOT NULL,
    business_date   DATE NOT NULL,
    qty_traded      BIGINT,
    deliv_qty       BIGINT,
    deliv_pct       DOUBLE,
    lake_key        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, series, business_date)
);

-- Articles fetched on demand from a filing's own link (providers/news_article.py).
-- Title, time and the publisher's summary only - never the body.
CREATE TABLE IF NOT EXISTS news_articles (
    url             VARCHAR PRIMARY KEY,
    domain          VARCHAR NOT NULL,
    title           VARCHAR,
    published_at    TIMESTAMP,
    excerpt         VARCHAR,
    http_status     INTEGER,
    lake_key        VARCHAR,
    fetched_at      TIMESTAMPTZ NOT NULL
);

-- Headline ratios compiled by Screener (Tier 3), with a cross-check against our own
-- Tier-1 close where one exists (ingest/fundamentals.py).
CREATE TABLE IF NOT EXISTS company_fundamentals (
    symbol          VARCHAR NOT NULL,
    fetched_on      DATE NOT NULL,
    company_name    VARCHAR,
    ratios          VARCHAR NOT NULL,      -- JSON {label: {raw, value, unit}}
    price_check     VARCHAR,               -- JSON {our_close, screener_price, drift, agrees}
    lake_key        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (symbol, fetched_on)
);

-- An invalidation condition that fired (decisions/monitor.py). Append-only: a thesis
-- breaking is a dated, cited fact, not a flag to be flipped back.
CREATE TABLE IF NOT EXISTS decision_alerts (
    decision_id     VARCHAR NOT NULL,
    as_of           DATE NOT NULL,
    check_name      VARCHAR NOT NULL,
    detail          VARCHAR,
    evidence_id     VARCHAR NOT NULL,
    raised_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (decision_id, evidence_id)
);

-- Attempts by untrusted text to steer a model (security/untrusted.py). Append-only:
-- a pattern across companies is only visible if nothing is overwritten.
CREATE TABLE IF NOT EXISTS security_findings (
    subject         VARCHAR NOT NULL,      -- company, ISIN or news_id the text came with
    where_seen      VARCHAR NOT NULL,      -- the boundary: tone_headline, dossier_fact...
    pattern         VARCHAR NOT NULL,
    matched         VARCHAR,
    excerpt         VARCHAR,
    action          VARCHAR NOT NULL,      -- rejected | fell back to trusted text
    detected_at     TIMESTAMPTZ NOT NULL
);

-- The document a filing points at, and what it says (C11, docintel/). The bytes live in
-- the lake; only the extracted facts and a locating snippet are stored here.
CREATE TABLE IF NOT EXISTS filing_documents (
    news_id         VARCHAR PRIMARY KEY,
    url             VARCHAR NOT NULL,
    pages           INTEGER,
    chars           INTEGER,
    status          VARCHAR NOT NULL,      -- ok | scanned (no text layer) | fetch failed...
    lake_key        VARCHAR,
    fetched_at      TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS filing_facts (
    news_id         VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL,      -- order_value | penalty | tax_demand | ...
    value           DOUBLE NOT NULL,
    unit            VARCHAR NOT NULL,      -- INR | USD | INR_per_share
    raw             VARCHAR,               -- the figure as the document writes it
    context         VARCHAR,               -- the sentence it sits in
    evidence_id     VARCHAR NOT NULL,
    extracted_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (news_id, kind)
);

-- As-reported financials, point in time (ingest/results.py). APPEND ONLY: a restatement
-- is a new row with a later filed_at, never an update, so "what did we know on date X"
-- stays answerable. Only statements that passed their own arithmetic are stored.
CREATE TABLE IF NOT EXISTS financial_results (
    isin            VARCHAR NOT NULL,
    company         VARCHAR,
    period_end      DATE NOT NULL,
    basis           VARCHAR NOT NULL,      -- consolidated | standalone
    filed_at        TIMESTAMP NOT NULL,    -- when the company published it
    news_id         VARCHAR,
    revenue         DOUBLE,                -- rupees, already multiplied out
    other_income    DOUBLE,
    total_income    DOUBLE,
    total_expenses  DOUBLE,
    pbt             DOUBLE,
    pat             DOUBLE,
    eps_basic       DOUBLE,                -- per share, not multiplied
    unit_multiplier BIGINT,                -- what the statement printed in
    checks_passed   BOOLEAN NOT NULL,
    lake_key        VARCHAR,
    source_page     INTEGER,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (isin, period_end, basis, filed_at)
);

-- What was knowable when a closed trade was entered (decisions/postmortem.py). One row
-- per closed trade, wins included: a rule learned from losses alone would also forbid
-- the wins that share their features.
CREATE TABLE IF NOT EXISTS decision_postmortems (
    decision_id         VARCHAR PRIMARY KEY,
    excess              DOUBLE,
    regime              VARCHAR,
    prompted_by         VARCHAR,           -- the event type that surfaced the company
    bucket              VARCHAR,           -- liquidity bucket at entry
    action              VARCHAR,
    invalidation_fired  BOOLEAN,           -- did a stated condition fire before the exit?
    features            VARCHAR,           -- JSON, everything knowable at entry
    created_at          TIMESTAMPTZ NOT NULL
);

-- Earnings-call transcripts and investor presentations (docintel/calls.py). Structure and
-- an embedding for retrieval; no reading of what was said, which would need its own
-- labelled set (ADR-0003).
CREATE TABLE IF NOT EXISTS call_documents (
    news_id          VARCHAR PRIMARY KEY,
    isin             VARCHAR,
    company          VARCHAR,
    called_on        DATE NOT NULL,
    event_type       VARCHAR,
    pages            INTEGER,
    chars            INTEGER,
    commentary_chars INTEGER,          -- prepared remarks: management chose every word
    qa_chars         INTEGER,          -- the part analysts chose
    has_qa           BOOLEAN,
    speakers         VARCHAR,          -- JSON list, in order of first appearance
    embedding        VARCHAR,          -- JSON vector from nomic-embed-text, or NULL
    lake_key         VARCHAR,
    fetched_at       TIMESTAMPTZ NOT NULL
);

-- What each replayed session showed the committee (evaluation/control.py), so the
-- control - the same universe bought blindly - can be priced later.
CREATE TABLE IF NOT EXISTS replay_candidates (
    session_date  DATE NOT NULL,
    isin          VARCHAR NOT NULL,
    company       VARCHAR,
    drafted       BOOLEAN NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (session_date, isin)
);

-- Timing models (evaluation/timing.py): a System One decision fitted on our own history.
-- Append-only: each fit keeps its own weights, threshold and out-of-sample report.
CREATE TABLE IF NOT EXISTS timing_models (
    version      VARCHAR NOT NULL,
    fitted_at    TIMESTAMPTZ NOT NULL,
    weights      VARCHAR NOT NULL,
    bias         DOUBLE NOT NULL,
    features     VARCHAR NOT NULL,
    threshold    DOUBLE,              -- NULL means the model declined to act at all
    fitted_on    VARCHAR,
    verified_on  VARCHAR,
    report       VARCHAR,
    PRIMARY KEY (version, fitted_at)
);

-- Forecast distributions (forecasting/store.py): what a model said, and what happened.
--
-- The paths are stored, not just the quantiles. First-passage questions - "is the target reached
-- before the stop" - are not answerable from a marginal distribution: two forecasts with identical
-- terminal quantiles can have opposite stop probabilities, because the answer depends on the ORDER
-- prices arrive in. Keeping p05..p95 and discarding the paths would silently throw away the only
-- questions a position with a stop actually asks.
--
-- Keyed on lineage rather than ISIN. 785 of this database's 16,217 lineages span more than one ISIN,
-- and an ISIN-keyed forecast loses its own history at a succession.
CREATE TABLE IF NOT EXISTS forecast_distributions (
    forecast_id   VARCHAR NOT NULL,     -- hash of (model, version, lineage, as_of, horizon, config)
    lineage       VARCHAR NOT NULL,
    as_of         DATE NOT NULL,        -- the last session the model was allowed to see
    horizon       INTEGER NOT NULL,     -- in exchange SESSIONS, never calendar days
    model         VARCHAR NOT NULL,
    model_version VARCHAR,
    made_at       TIMESTAMPTZ NOT NULL,
    anchor        DOUBLE NOT NULL,      -- the close at as_of, which every path starts from
    n_paths       INTEGER NOT NULL,
    paths         DOUBLE[][],
    config        VARCHAR,              -- temperature, top_p, context, seed: searched parameters
    data_version  VARCHAR,
    -- Filled in once the horizon has elapsed. NULL means not yet resolvable, which is different
    -- from a forecast that had no realised price, and the distinction has to survive in the table:
    -- treating "too recent to score" as "unscoreable" is how a study quietly drops its most recent
    -- and least favourable observations.
    realised      DOUBLE,
    realised_at   DATE,
    scored_at     TIMESTAMPTZ,
    crps          DOUBLE,
    pit           DOUBLE,
    PRIMARY KEY (forecast_id)
);

-- Opportunity memory (opportunity/memory.py): what the system considered, not only what it traded.
--
-- A system that records only its trades learns from a sample it selected itself. It can measure how
-- its positions did and can never measure what it declined - so a rule that systematically rejects
-- good opportunities is invisible to it, because the evidence that would expose the rule is exactly
-- what the rule threw away.
--
-- One row per candidate per rebalance: everything the ranking saw, what happened to it, and why. The
-- rejected rows are the point. They are also the majority - a top-100 book over a 1,700-name universe
-- rejects sixteen names for every one it takes - so this table grows fast and is written in bulk.
CREATE TABLE IF NOT EXISTS opportunity_memory (
    run_id        VARCHAR NOT NULL,   -- the paper run's experiment id
    session       DATE NOT NULL,      -- the rebalance session
    signal_from   DATE NOT NULL,      -- the session the ranking was computed on
    lineage       VARCHAR NOT NULL,
    rank          INTEGER,            -- position in the ranking; NULL when not ranked at all
    score         DOUBLE,             -- the feature value the ranking used
    -- TAKEN, HELD, REJECTED_RANK (ranked below the cut), REJECTED_FILTER (an exclusion fired),
    -- REJECTED_CASH (the book ran out of money before reaching it), EXITED.
    disposition   VARCHAR NOT NULL,
    reason        VARCHAR,
    -- Filled by resolve() once the horizon closes. The counterfactual: what the name did whether or
    -- not it was held. NULL means unresolved, which is different from a name that stopped trading.
    fwd_return    DOUBLE,
    fwd_excess    DOUBLE,             -- against the eligible universe over the same window
    resolved_at   TIMESTAMPTZ,
    unresolvable  BOOLEAN,
    PRIMARY KEY (run_id, session, lineage)
);

-- Research-only immutable decision context for feasible counterfactual analysis.
-- State and constraints are recorded as they were known at decision time. Never
-- overwrite a row with information learned after the decision.
CREATE TABLE IF NOT EXISTS inverse_decision_context (
    context_id       VARCHAR PRIMARY KEY,
    decision_ts      TIMESTAMPTZ NOT NULL,
    state_asof       TIMESTAMPTZ NOT NULL,
    instrument       VARCHAR NOT NULL,
    action           VARCHAR NOT NULL,
    state_json       VARCHAR NOT NULL,
    constraints_json VARCHAR NOT NULL,
    alternatives_json VARCHAR NOT NULL,
    provenance       VARCHAR NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL
);

-- One immutable snapshot per paper rebalance; session-level timing is explicit.
CREATE TABLE IF NOT EXISTS paper_rebalance_context (
    run_id           VARCHAR NOT NULL,
    execution_session DATE NOT NULL,
    signal_session   DATE NOT NULL,
    content_hash     VARCHAR NOT NULL,
    candidates_json  VARCHAR NOT NULL,
    constraints_json VARCHAR NOT NULL,
    portfolio_json   VARCHAR NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, execution_session)
);


-- Every results PDF the bulk loader has tried, so a resumed run skips what already failed or had no
-- statement instead of re-downloading it.
CREATE TABLE IF NOT EXISTS results_attempts (
    news_id      VARCHAR PRIMARY KEY,
    status       VARCHAR NOT NULL,     -- stored | no_statement | fetch_failed | parse_failed
    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL
);


-- Exchange XBRL results (ingest/xbrl_results.py). One row per listed filing, whatever became of it, so
-- coverage is a query. filed_at is the exchange's dissemination time: the point-in-time key.
CREATE TABLE IF NOT EXISTS xbrl_filings (
    seq_number   VARCHAR PRIMARY KEY,
    symbol       VARCHAR,
    isin         VARCHAR,
    company      VARCHAR,
    period_start DATE,
    period_end   DATE,
    basis        VARCHAR,              -- consolidated | standalone
    relating_to  VARCHAR,
    audited      VARCHAR,
    cumulative   BOOLEAN,
    is_bank      BOOLEAN,
    filed_at     TIMESTAMP,
    xbrl_url     VARCHAR,
    lake_key     VARCHAR,
    status       VARCHAR NOT NULL,     -- stored | no_xbrl | fetch_failed | parse_failed | period_mismatch
    attempted_at TIMESTAMP WITH TIME ZONE
);

-- Every company-level numeric fact, long form. ctx: Q current period, YTD year to date, I balance-sheet
-- instant, I_PRIOR the comparative instant. Values are rupees already multiplied out; EPS per share.
CREATE TABLE IF NOT EXISTS xbrl_facts (
    seq_number VARCHAR NOT NULL,
    concept    VARCHAR NOT NULL,
    ctx        VARCHAR NOT NULL,
    value      DOUBLE,
    PRIMARY KEY (seq_number, concept, ctx)
);

-- NSE structured insider-trading disclosures (ingest/insider_xbrl.py): SEBI PIT XBRL, one row per disclosure in a
-- filing. A forward record from July 2026 (the feed has no earlier history). A revision is its own filing (app_id).
CREATE TABLE IF NOT EXISTS insider_disclosures (
    app_id          VARCHAR NOT NULL,
    disclosure      VARCHAR NOT NULL,
    symbol          VARCHAR,
    isin            VARCHAR,
    company         VARCHAR,
    regulation      VARCHAR,
    revised         BOOLEAN,
    submission      VARCHAR,
    disseminated_at TIMESTAMP,
    category        VARCHAR,
    person          VARCHAR,
    instrument      VARCHAR,
    side            VARCHAR,
    qty             DOUBLE,
    value_inr       DOUBLE,
    held_before     DOUBLE,
    pct_before      DOUBLE,
    held_after      DOUBLE,
    pct_after       DOUBLE,
    mode            VARCHAR,
    trade_from      DATE,
    trade_to        DATE,
    intimated_on    DATE,
    exchange        VARCHAR,
    lake_key        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (app_id, disclosure)
);

-- NSE F&O ban list per trade date (ingest/nse_archives.py). fo_ban_days records every day read, so a day with an
-- empty ban list is distinguishable from a day never collected.
CREATE TABLE IF NOT EXISTS fo_ban (
    trade_date      DATE NOT NULL,
    symbol          VARCHAR NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);
CREATE TABLE IF NOT EXISTS fo_ban_days (
    trade_date      DATE PRIMARY KEY,
    n               INTEGER,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- NSE participant-wise open interest and volume in equity derivatives (Client / DII / FII / Pro / TOTAL), contracts.
CREATE TABLE IF NOT EXISTS participant_positions (
    trade_date      DATE NOT NULL,
    kind            VARCHAR NOT NULL,      -- oi | volume
    client_type     VARCHAR NOT NULL,
    measure         VARCHAR NOT NULL,      -- e.g. 'Future Index Long'
    contracts       DOUBLE,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trade_date, kind, client_type, measure)
);

-- NSE shareholding patterns (ingest/shareholding.py), from the September-2021 quarter. known_at = the later of the
-- broadcast and submission times: the filing is usable from then. Facts are category totals only (no holder names).
CREATE TABLE IF NOT EXISTS shareholding_filings (
    record_id       VARCHAR PRIMARY KEY,
    symbol          VARCHAR,
    isin            VARCHAR,
    period_end      DATE,
    known_at        TIMESTAMP,
    broadcast_at    TIMESTAMP,
    submitted_at    TIMESTAMP,
    revised         VARCHAR,
    promoter_pct    DOUBLE,
    public_pct      DOUBLE,
    lake_key        VARCHAR,
    facts           INTEGER,
    observed_at     TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS shareholding_facts (
    record_id       VARCHAR NOT NULL,
    category        VARCHAR NOT NULL,      -- e.g. MutualFundsOrUTI, InstitutionsForeignPortfolioInvestorCategoryOne
    concept         VARCHAR NOT NULL,      -- e.g. ShareholdingAsAPercentageOfTotalNumberOfShares
    value           DOUBLE,
    PRIMARY KEY (record_id, category, concept)
);

-- NSE bulk and block deals and short-selling disclosures (ingest/bulk_deals.py). seq keeps two identical lines of
-- one day's file apart; a month's file replaces that month.
CREATE TABLE IF NOT EXISTS bulk_block_deals (
    kind            VARCHAR NOT NULL,      -- bulk | block
    trade_date      DATE NOT NULL,
    seq             INTEGER NOT NULL,
    symbol          VARCHAR,
    security        VARCHAR,
    client          VARCHAR,
    side            VARCHAR,
    qty             DOUBLE,
    price           DOUBLE,
    remarks         VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (kind, trade_date, seq)
);
CREATE TABLE IF NOT EXISTS short_selling (
    trade_date      DATE NOT NULL,
    symbol          VARCHAR,
    security        VARCHAR,
    qty             DOUBLE,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Macro series from FRED (ingest/macro.py). obs_date is the observation period, NOT the release date: lag each series
-- by release_lag_days before using it, and treat monthly values as revised.
CREATE TABLE IF NOT EXISTS macro_series (
    series          VARCHAR NOT NULL,
    obs_date        DATE NOT NULL,
    value           DOUBLE,
    description     VARCHAR,
    release_lag_days INTEGER,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (series, obs_date)
);

-- NSE index total-return series (gross TRI and net TRI) and constituent snapshots (ingest/index_tri.py). Membership
-- history is only what has been snapshotted since 2026-10-18: niftyindices publishes the current list only.
CREATE TABLE IF NOT EXISTS index_total_return (
    index_name      VARCHAR NOT NULL,
    business_date   DATE NOT NULL,
    tri             DOUBLE,
    ntr             DOUBLE,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (index_name, business_date)
);
CREATE TABLE IF NOT EXISTS index_membership (
    index_name      VARCHAR NOT NULL,
    as_of           DATE NOT NULL,
    symbol          VARCHAR,
    isin            VARCHAR,
    industry        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Daily FII/FPI and DII cash-market flows, Rs crore (ingest/flows.py). A forward record from 2026-10-18.
CREATE TABLE IF NOT EXISTS fii_dii_flows (
    trade_date      DATE NOT NULL,
    category        VARCHAR NOT NULL,
    buy_cr          DOUBLE,
    sell_cr         DOUBLE,
    net_cr          DOUBLE,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trade_date, category)
);

-- Structured NSE disclosure listings (ingest/nse_disclosures.py): corporate actions, SAST Reg. 29 acquisitions and
-- disposals, Reg. 31/32 pledges, encumbrance declarations, the event calendar. The row as published (payload JSON),
-- keyed by its content hash; usable from broadcast_at where the listing gives one.
CREATE TABLE IF NOT EXISTS nse_disclosures (
    dataset         VARCHAR NOT NULL,
    record_key      VARCHAR NOT NULL,
    event_date      DATE,
    broadcast_at    TIMESTAMP,
    symbol          VARCHAR,
    isin            VARCHAR,
    company         VARCHAR,
    payload         VARCHAR,
    lake_key        VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset, record_key)
);

-- SEBI RSS items (ingest/sebi.py): orders, enforcement, circulars. A forward record from 2026-10-18.
CREATE TABLE IF NOT EXISTS sebi_items (
    link            VARCHAR PRIMARY KEY,
    title           VARCHAR,
    published       DATE,
    section         VARCHAR,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Credit-rating announcements with their direction (events/rating_direction.py), rebuilt from announcements.
CREATE TABLE IF NOT EXISTS rating_actions (
    news_id         VARCHAR PRIMARY KEY,
    isin            VARCHAR,
    business_date   DATE,
    published_at    TIMESTAMP,
    direction       VARCHAR,
    sign            INTEGER,
    text            VARCHAR
);

-- Official CPI and IIP from MoSPI (ingest/mospi.py). available_on is the conservative first day a value could be
-- known (release-calendar rule); use it, never the period, in any signal.
CREATE TABLE IF NOT EXISTS official_macro (
    series          VARCHAR NOT NULL,      -- CPI | IIP
    period          DATE NOT NULL,         -- first day of the month measured
    base_year       VARCHAR NOT NULL,
    category        VARCHAR NOT NULL,      -- CPI: Rural/Urban/Combined; IIP: General/Sectoral/Use-based
    subcategory     VARCHAR NOT NULL,
    value           DOUBLE,
    growth          DOUBLE,                -- CPI inflation % / IIP growth %, year on year
    status          VARCHAR,               -- CPI: P provisional, F final
    available_on    DATE,
    observed_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (series, period, base_year, category, subcategory)
);

-- Rating direction read from the attached letter where the headline gave none (ingest/rating_docs.py).
CREATE TABLE IF NOT EXISTS rating_pdf_direction (
    news_id         VARCHAR PRIMARY KEY,
    direction       VARCHAR,
    sign            INTEGER,
    chars           INTEGER,
    observed_at     TIMESTAMPTZ NOT NULL
);

-- Monthly business updates (auto sales, cement despatches, power generation, loan book / AUM updates...) found among
-- the announcements by their own subject line (ingest/monthly_updates.py). The figures stay in the attachment.
CREATE TABLE IF NOT EXISTS monthly_updates (
    news_id         VARCHAR PRIMARY KEY,
    isin            VARCHAR,
    company         VARCHAR,
    business_date   DATE,
    published_at    TIMESTAMP,
    kind            VARCHAR,
    headline        VARCHAR,
    attachment      VARCHAR
);
