-- ============================================================================
-- SEBI-Compliant Algo Trading System — Initial Database Schema
-- Target: PostgreSQL 15+ / Supabase
-- Run via: psql -f migrations/001_initial_schema.sql  OR  Supabase SQL Editor
-- ============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ────────────────────────────────────────────────────────────────────────────
-- 1. USERS
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name             VARCHAR(255) NOT NULL,
    email                 VARCHAR(255) UNIQUE NOT NULL,
    broker                VARCHAR(50)  NOT NULL DEFAULT 'zerodha',
    broker_client_id      VARCHAR(50)  NOT NULL,
    kite_access_token     TEXT,
    kite_token_generated_at TIMESTAMPTZ,
    total_capital         NUMERIC(14,2) DEFAULT 0,
    risk_per_trade_pct    NUMERIC(5,2)  DEFAULT 1.0,
    max_daily_loss        NUMERIC(14,2) DEFAULT 1000,
    max_trades_per_day    INTEGER       DEFAULT 3,
    cooldown_minutes      INTEGER       DEFAULT 20,
    is_active             BOOLEAN       DEFAULT TRUE,
    created_at            TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_broker_client ON users (broker_client_id);
CREATE INDEX IF NOT EXISTS idx_users_active ON users (is_active) WHERE is_active = TRUE;

-- ────────────────────────────────────────────────────────────────────────────
-- 2. STRATEGIES
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategies (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                  VARCHAR(255) NOT NULL,
    algo_id               VARCHAR(100) NOT NULL,  -- exchange-assigned Algo-ID (SEBI mandate)
    broker_strategy_ref   VARCHAR(255),
    webhook_token         VARCHAR(255) UNIQUE NOT NULL,
    default_stoploss_pct  NUMERIC(5,2)  DEFAULT 0.5,
    default_target_pct    NUMERIC(5,2)  DEFAULT 1.0,
    is_active             BOOLEAN       DEFAULT TRUE,
    created_at            TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_strategies_user ON strategies (user_id);
CREATE INDEX IF NOT EXISTS idx_strategies_webhook ON strategies (webhook_token);
CREATE INDEX IF NOT EXISTS idx_strategies_active ON strategies (is_active) WHERE is_active = TRUE;

-- ────────────────────────────────────────────────────────────────────────────
-- 3. TRADES
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_id           UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    symbol                VARCHAR(50)  NOT NULL,
    exchange              VARCHAR(10)  DEFAULT 'NSE',
    direction             VARCHAR(4)   NOT NULL,        -- BUY / SELL
    quantity              INTEGER      NOT NULL,
    entry_price           NUMERIC(14,2),
    stoploss_price        NUMERIC(14,2),
    target_price          NUMERIC(14,2),
    exit_price            NUMERIC(14,2),
    pnl                   NUMERIC(14,2) DEFAULT 0,
    status                VARCHAR(20)   DEFAULT 'OPEN', -- OPEN / CLOSED / CANCELLED
    algo_id               VARCHAR(100)  NOT NULL,       -- SEBI Algo-ID tag
    opened_at             TIMESTAMPTZ   DEFAULT now(),
    closed_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_trades_user_date ON trades (user_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_strategy   ON trades (strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades (symbol, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_open       ON trades (user_id, status) WHERE status = 'OPEN';

-- ────────────────────────────────────────────────────────────────────────────
-- 4. ORDERS
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_id              UUID NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    broker_order_id       VARCHAR(100),
    order_type            VARCHAR(20) NOT NULL,          -- ENTRY / STOPLOSS / TARGET / EXIT / MODIFY / CANCEL
    transaction_type      VARCHAR(4)  NOT NULL,          -- BUY / SELL
    product               VARCHAR(10) DEFAULT 'MIS',     -- MIS / CNC / NRML
    quantity              INTEGER     NOT NULL,
    price                 NUMERIC(14,2),
    trigger_price         NUMERIC(14,2),
    status                VARCHAR(20) DEFAULT 'PENDING', -- PENDING / PLACED / COMPLETE / REJECTED / CANCELLED
    algo_id               VARCHAR(100) NOT NULL,         -- SEBI Algo-ID tag
    raw_response          JSONB,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_orders_trade       ON orders (trade_id);
CREATE INDEX IF NOT EXISTS idx_orders_status      ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_broker_id   ON orders (broker_order_id);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. LOGS
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS logs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID REFERENCES users(id) ON DELETE SET NULL,
    level                 VARCHAR(10)  DEFAULT 'INFO',  -- DEBUG / INFO / WARNING / ERROR / CRITICAL
    source                VARCHAR(100) NOT NULL,
    message               TEXT         NOT NULL,
    context               JSONB,
    created_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_logs_level_date ON logs (level, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_user       ON logs (user_id);

-- ────────────────────────────────────────────────────────────────────────────
-- 6. NOTIFICATIONS
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID REFERENCES users(id) ON DELETE SET NULL,
    channel               VARCHAR(50)  DEFAULT 'telegram',
    category              VARCHAR(50)  NOT NULL,  -- TRADE_EXECUTED / ERROR / DAILY_SUMMARY / TOKEN_REFRESH
    payload               JSONB        NOT NULL,
    delivered             BOOLEAN      DEFAULT FALSE,
    created_at            TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user     ON notifications (user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_category ON notifications (category, created_at DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- HELPER VIEWS (optional — useful for risk engine queries)
-- ────────────────────────────────────────────────────────────────────────────

-- Today's closed P&L per user
CREATE OR REPLACE VIEW v_daily_pnl AS
SELECT
    user_id,
    DATE(opened_at AT TIME ZONE 'Asia/Kolkata') AS trade_date,
    COUNT(*)                                     AS trade_count,
    COALESCE(SUM(pnl), 0)                        AS total_pnl
FROM trades
WHERE status IN ('CLOSED', 'OPEN')
GROUP BY user_id, DATE(opened_at AT TIME ZONE 'Asia/Kolkata');

-- Today's trade count per user (includes all statuses)
CREATE OR REPLACE VIEW v_daily_trade_count AS
SELECT
    user_id,
    DATE(opened_at AT TIME ZONE 'Asia/Kolkata') AS trade_date,
    COUNT(*)                                     AS trade_count
FROM trades
GROUP BY user_id, DATE(opened_at AT TIME ZONE 'Asia/Kolkata');
