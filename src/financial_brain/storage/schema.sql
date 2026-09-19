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
    reasons         VARCHAR[]
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
    classified_at   TIMESTAMPTZ NOT NULL
);
