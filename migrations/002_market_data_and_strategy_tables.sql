-- ============================================================================
-- SEBI-Compliant Algo Trading System — Market Data & Strategy Engine Tables
-- Migration: 002_market_data_and_strategy_tables.sql
-- Target: PostgreSQL 15+ / Supabase
-- Run via: psql -f migrations/002_market_data_and_strategy_tables.sql  OR  Supabase SQL Editor
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. MARKET_DATA — Raw OHLCV candles for watched instruments
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_data (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  NOT NULL DEFAULT 'NSE',
    interval              VARCHAR(10)  NOT NULL,           -- 1m, 5m, 15m, 1h, 1d
    open                  NUMERIC(14,2) NOT NULL,
    high                  NUMERIC(14,2) NOT NULL,
    low                   NUMERIC(14,2) NOT NULL,
    close                 NUMERIC(14,2) NOT NULL,
    volume                BIGINT       NOT NULL DEFAULT 0,
    timestamp             TIMESTAMPTZ  NOT NULL,           -- Candle open time (IST)
    source                VARCHAR(50)  NOT NULL DEFAULT 'kite',  -- Data source
    created_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_market_data_symbol_interval_ts
    ON market_data (symbol, exchange, interval, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_market_data_ts
    ON market_data (timestamp DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_data_candle
    ON market_data (symbol, exchange, interval, timestamp);

-- ────────────────────────────────────────────────────────────────────────────
-- 2. REGIME_SNAPSHOT — Market regime classification per instrument
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  NOT NULL DEFAULT 'NSE',
    regime                VARCHAR(30)  NOT NULL,           -- trending-up / trending-down / range-bound / high-volatility
    adx                   NUMERIC(8,2),                    -- ADX value
    atr                   NUMERIC(14,2),                   -- ATR value
    bb_bandwidth          NUMERIC(8,4),                    -- Bollinger bandwidth
    ema_slope             NUMERIC(14,6),                   -- EMA slope (direction)
    confidence            NUMERIC(4,2) DEFAULT 0.5,        -- 0.0 - 1.0
    metadata              JSONB,                           -- Additional indicator values
    timestamp             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_regime_symbol_ts
    ON regime_snapshots (symbol, exchange, timestamp DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 3. STRATEGY_SIGNALS — Raw signals emitted by each strategy
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_signals (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_name         VARCHAR(100) NOT NULL,
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  NOT NULL DEFAULT 'NSE',
    action                VARCHAR(10)  NOT NULL,           -- BUY / SELL / HOLD / EXIT
    confidence_score      NUMERIC(4,2) NOT NULL,           -- 0.00 - 1.00
    entry_price           NUMERIC(14,2),
    stoploss_price        NUMERIC(14,2),
    target_price          NUMERIC(14,2),
    suggested_quantity    INTEGER,
    regime_at_signal      VARCHAR(30),                     -- Regime when signal generated
    metadata              JSONB,                           -- Indicator snapshots
    timestamp             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_strategy_signals_strategy_ts
    ON strategy_signals (strategy_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_signals_symbol_ts
    ON strategy_signals (symbol, exchange, timestamp DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. RECOMMENDATIONS — Ranked + risk-adjusted recommendations (dashboard reads this)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  NOT NULL DEFAULT 'NSE',
    direction             VARCHAR(4)   NOT NULL,           -- BUY / SELL
    entry_price           NUMERIC(14,2) NOT NULL,
    stoploss_price        NUMERIC(14,2) NOT NULL,
    target_price          NUMERIC(14,2) NOT NULL,
    quantity              INTEGER      NOT NULL,
    capital_at_risk       NUMERIC(14,2) NOT NULL,
    risk_reward_ratio     NUMERIC(6,2),
    confidence_score      NUMERIC(4,2) NOT NULL,           -- Composite confidence
    regime                VARCHAR(30)  NOT NULL,
    top_strategy_name     VARCHAR(100) NOT NULL,           -- Primary strategy driving this
    ai_summary            TEXT,                            -- OpenAI-generated rationale
    ai_key_risks          TEXT,                            -- Key risks from AI
    ai_invalidation       TEXT,                            -- What would invalidate setup
    status                VARCHAR(20)  DEFAULT 'PENDING',  -- PENDING / EXECUTED / REJECTED / EXPIRED / CANCELLED
    executed_at           TIMESTAMPTZ,
    executed_trade_id     UUID,                            -- Reference to trades table
    expires_at            TIMESTAMPTZ,                     -- Auto-expiry (e.g. EOD)
    created_at            TIMESTAMPTZ  DEFAULT now(),
    updated_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_status_ts
    ON recommendations (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_symbol_ts
    ON recommendations (symbol, exchange, created_at DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. PERFORMANCE_SNAPSHOTS — Daily equity curve & per-strategy metrics
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS performance_snapshots (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_name         VARCHAR(100),                    -- NULL = aggregate/portfolio level
    symbol                VARCHAR(50),                     -- NULL = aggregate across symbols
    date                  DATE       NOT NULL,             -- IST date
    starting_capital      NUMERIC(14,2) NOT NULL,
    ending_capital        NUMERIC(14,2) NOT NULL,
    realized_pnl          NUMERIC(14,2) DEFAULT 0,
    unrealized_pnl        NUMERIC(14,2) DEFAULT 0,
    total_pnl             NUMERIC(14,2) GENERATED ALWAYS AS (realized_pnl + unrealized_pnl) STORED,
    trades_count          INTEGER      DEFAULT 0,
    wins                  INTEGER      DEFAULT 0,
    losses                INTEGER      DEFAULT 0,
    max_drawdown          NUMERIC(14,2) DEFAULT 0,
    max_drawdown_pct      NUMERIC(6,2) DEFAULT 0,
    sharpe_ratio          NUMERIC(6,3),
    win_rate              NUMERIC(5,2) GENERATED ALWAYS AS (
        CASE WHEN trades_count > 0 THEN (wins::numeric / trades_count * 100) ELSE 0 END
    ) STORED,
    expectancy            NUMERIC(14,2),                   -- Avg win * win_rate - Avg loss * loss_rate
    created_at            TIMESTAMPTZ  DEFAULT now(),
    updated_at            TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (user_id, strategy_name, symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_perf_user_date
    ON performance_snapshots (user_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_perf_strategy_date
    ON performance_snapshots (strategy_name, date DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 6. WATCHLIST — Configurable instrument watchlist for data fetching
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  NOT NULL DEFAULT 'NSE',
    instrument_token      INTEGER,                         -- Kite instrument token (resolved)
    intervals             VARCHAR(100) DEFAULT '5m,15m,1h', -- Comma-separated intervals to fetch
    is_active             BOOLEAN      DEFAULT TRUE,
    priority              INTEGER      DEFAULT 0,          -- Higher = fetch more frequently
    created_at            TIMESTAMPTZ  DEFAULT now(),
    updated_at            TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (user_id, symbol, exchange)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user_active
    ON watchlist (user_id, is_active) WHERE is_active = TRUE;

-- ────────────────────────────────────────────────────────────────────────────
-- 7. DAILY_REPORTS — Archived end-of-day reports
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_reports (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date           DATE       NOT NULL,
    total_pnl             NUMERIC(14,2) DEFAULT 0,
    trades_count          INTEGER      DEFAULT 0,
    wins                  INTEGER      DEFAULT 0,
    losses                INTEGER      DEFAULT 0,
    max_drawdown          NUMERIC(14,2) DEFAULT 0,
    best_trade_pnl        NUMERIC(14,2) DEFAULT 0,
    worst_trade_pnl       NUMERIC(14,2) DEFAULT 0,
    best_strategy         VARCHAR(100),
    worst_strategy        VARCHAR(100),
    regime_summary        JSONB,                           -- Regime distribution for the day
    strategy_performance  JSONB,                           -- Per-strategy P&L, win rate
    ai_summary            TEXT,                            -- AI-generated daily narrative
    telegram_sent         BOOLEAN      DEFAULT FALSE,
    created_at            TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (user_id, report_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_reports_user_date
    ON daily_reports (user_id, report_date DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- HELPER VIEWS
-- ────────────────────────────────────────────────────────────────────────────

-- Latest regime per symbol
CREATE OR REPLACE VIEW v_latest_regime AS
SELECT DISTINCT ON (symbol, exchange)
    symbol,
    exchange,
    regime,
    adx,
    atr,
    bb_bandwidth,
    ema_slope,
    confidence,
    metadata,
    timestamp
FROM regime_snapshots
ORDER BY symbol, exchange, timestamp DESC;

-- Latest recommendation per symbol (pending only)
CREATE OR REPLACE VIEW v_latest_pending_recommendation AS
SELECT DISTINCT ON (symbol, exchange)
    id,
    symbol,
    exchange,
    direction,
    entry_price,
    stoploss_price,
    target_price,
    quantity,
    capital_at_risk,
    confidence_score,
    regime,
    top_strategy_name,
    ai_summary,
    created_at,
    expires_at
FROM recommendations
WHERE status = 'PENDING' AND (expires_at IS NULL OR expires_at > now())
ORDER BY symbol, exchange, created_at DESC;

-- Portfolio equity curve (daily)
CREATE OR REPLACE VIEW v_equity_curve AS
SELECT
    user_id,
    date,
    ending_capital AS equity,
    total_pnl,
    trades_count,
    win_rate,
    max_drawdown_pct
FROM performance_snapshots
WHERE strategy_name IS NULL AND symbol IS NULL
ORDER BY user_id, date;