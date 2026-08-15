-- ============================================================================
-- SEBI-Compliant Algo Trading System — Audit Trail Schema
-- Migration: 002_audit_trail.sql
-- Target: PostgreSQL 15+ / Supabase
-- Run via: psql -f migrations/002_audit_trail.sql  OR  Supabase SQL Editor
--
-- This table stores immutable, append-only audit records required by SEBI
-- for regulatory inspections of algorithmic trading activity.
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_trail (
    -- Primary key
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Timestamp: high-precision IST for regulatory compliance
    timestamp             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Actor identification
    user_id               UUID REFERENCES users(id) ON DELETE SET NULL,
    algo_id               VARCHAR(100),               -- SEBI-mandated Algo-ID

    -- Action classification
    action                VARCHAR(50) NOT NULL,        -- ORDER_PLACED / ORDER_FILLED / ORDER_REJECTED /
                                                       -- ORDER_CANCELLED / RISK_APPROVED / RISK_REJECTED /
                                                       -- POSITION_CLOSED / SYSTEM_EVENT / TOKEN_REFRESH /
                                                       -- EOD_SQUAREOFF / RECONCILIATION_MISMATCH

    -- Instrument details
    symbol                VARCHAR(50),
    exchange              VARCHAR(10),
    direction             VARCHAR(4),                  -- BUY / SELL
    quantity              INTEGER,
    price                 NUMERIC(14,2),

    -- Broker linkage
    broker_order_id       VARCHAR(100),

    -- Risk engine decision
    risk_decision         VARCHAR(20),                 -- APPROVED / REJECTED
    risk_reason           TEXT,                        -- Human-readable rejection reason

    -- Full payloads for post-incident analysis
    raw_payload           JSONB,                       -- Webhook body, broker response, etc.

    -- Provenance
    source                VARCHAR(100),                -- WEBHOOK / POSTBACK / EOD_SQUAREOFF / MANUAL / SYSTEM
    trade_id              UUID,                        -- Logical reference (not FK — immutable audit data)
    order_id              UUID,                        -- Logical reference (not FK — immutable audit data)
    ip_address            VARCHAR(45),                 -- IPv4/IPv6 of the triggering client

    -- Extensible metadata
    extra                 JSONB                        -- Any additional context data
);

-- ── Indexes for common query patterns ──────────────────────────────────────

-- SEBI inspectors query by user + date range
CREATE INDEX IF NOT EXISTS idx_audit_user_date
    ON audit_trail (user_id, timestamp DESC);

-- Filter by action type (e.g. find all rejections)
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_trail (action, timestamp DESC);

-- Cross-reference with Algo-ID registry
CREATE INDEX IF NOT EXISTS idx_audit_algo_id
    ON audit_trail (algo_id);

-- Symbol-level audit (e.g. "show all activity for RELIANCE")
CREATE INDEX IF NOT EXISTS idx_audit_symbol
    ON audit_trail (symbol, timestamp DESC);

-- Broker order correlation
CREATE INDEX IF NOT EXISTS idx_audit_broker_order
    ON audit_trail (broker_order_id);

-- Trade-level audit trail reconstruction
CREATE INDEX IF NOT EXISTS idx_audit_trade_id
    ON audit_trail (trade_id);

-- ── Immutability protection ────────────────────────────────────────────────
-- Prevent UPDATE and DELETE on audit_trail.  Records are append-only.
-- This trigger ensures regulatory integrity even if application code
-- has a bug.  To disable for maintenance, use: ALTER TABLE audit_trail DISABLE TRIGGER audit_immutable;

CREATE OR REPLACE FUNCTION fn_audit_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_trail records are immutable — UPDATE and DELETE are prohibited for SEBI compliance';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_immutable ON audit_trail;
CREATE TRIGGER audit_immutable
    BEFORE UPDATE OR DELETE ON audit_trail
    FOR EACH ROW
    EXECUTE FUNCTION fn_audit_immutable();

-- ── Helper view: daily audit summary ───────────────────────────────────────
CREATE OR REPLACE VIEW v_audit_daily_summary AS
SELECT
    user_id,
    DATE(timestamp AT TIME ZONE 'Asia/Kolkata') AS audit_date,
    action,
    COUNT(*) AS event_count
FROM audit_trail
GROUP BY user_id, DATE(timestamp AT TIME ZONE 'Asia/Kolkata'), action
ORDER BY audit_date DESC, action;
