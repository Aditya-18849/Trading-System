# SEBI-Compliant Algo Trading System

A complete automated trading platform for Indian markets with:
- **TradingView webhook ingestion** → Risk engine → Broker execution (Zerodha Kite Connect)
- **Internal Strategy Engine** → 10 strategies → Regime detection → AI summaries → Dashboard
- **SEBI Algo-ID tagging** on every order
- **Real-time portfolio dashboard** (Next.js + React)

---

## Architecture

```
TradingView Alert ──► Webhook ──► Risk Engine ──► Kite Connect ──► DB + Telegram
                          │
                    ┌─────┴─────┐
                    ▼           ▼
            Market Data      Strategy Engine
            (Kite)      ──► 10 Strategies
                    │           │
                    ▼           ▼
            Regime Detect   Rank + AI
                    │           │
                    ▼           ▼
              Dashboard ◄── Recommendation
                    │
                    ▼
             Manual Execute ──► Risk Engine ──► Kite Connect
```

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Zerodha Kite Connect API credentials
- Telegram Bot token & chat ID
- (Optional) OpenAI API key for AI summaries

### 1. Clone & Configure
```bash
cd trading-system
cp .env.example .env
# Edit .env with your credentials
```

### 2. Start with Docker Compose
```bash
docker-compose up -d --build
```

This starts:
- **PostgreSQL** on port 5432
- **Redis** on port 6379
- **FastAPI Backend** on port 8000
- **Next.js Frontend** on port 3000

### 3. Access Dashboard
Open http://localhost:3000

### 4. Run Daily Token Refresh (once)
```bash
docker-compose exec app python scripts/daily_token_refresh.py
```

---

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | Postgres connection string | Yes |
| `TRADINGVIEW_WEBHOOK_SECRET` | Shared secret for webhook validation | Yes |
| `KITE_API_KEY` | Zerodha API key | Yes |
| `KITE_API_SECRET` | Zerodha API secret | Yes |
| `KITE_USER_ID` | Zerodha user ID | Yes |
| `KITE_PASSWORD` | Zerodha password | Yes |
| `KITE_TOTP_SECRET` | TOTP secret for 2FA | Yes |
| `KITE_ALGO_ID` | SEBI Algo-ID from exchange | Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | Yes |
| `TELEGRAM_CHAT_ID` | Target chat ID | Yes |
| `OPENAI_API_KEY` | For AI summaries (optional) | No |
| `ADMIN_API_KEY` | For admin endpoints | Yes |

---

## Project Structure

```
trading-system/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, webhook, lifespan
│   │   ├── config.py               # Pydantic settings
│   │   ├── database.py             # SQLAlchemy engine
│   │   ├── models.py               # ORM models (8 tables)
│   │   ├── schemas.py              # Pydantic schemas
│   │   ├── risk_engine.py          # Risk checks + calculate_risk_for_signal
│   │   ├── scheduler.py            # APScheduler market open/close
│   │   ├── broker/
│   │   │   ├── base.py             # Abstract broker adapter
│   │   │   ├── kite.py             # Kite Connect order placement
│   │   │   └── kite_data.py        # Market data fetching
│   │   ├── market_data/
│   │   │   └── fetcher.py          # Watchlist polling service
│   │   ├── strategy_engine/
│   │   │   ├── base.py             # Strategy ABC + indicators
│   │   │   ├── regime.py           # Market regime detection
│   │   │   ├── ranker.py           # Signal ranking
│   │   │   ├── ai_summary.py       # OpenAI summaries
│   │   │   ├── recommender.py      # Final recommendations
│   │   │   └── strategies/         # 10 strategy implementations
│   │   ├── analytics/
│   │   │   └── performance.py      # Equity curve, metrics
│   │   ├── reports/
│   │   │   └── daily_report.py     # EOD report generator
│   │   ├── routers/
│   │   │   └── dashboard.py        # Dashboard API endpoints
│   │   ├── notifications/
│   │   │   └── telegram.py         # Telegram bot
│   │   └── utils/
│   │       └── logging.py          # Structured logging
│   ├── scripts/
│   │   └── daily_token_refresh.py  # OAuth + TOTP automation
│   ├── migrations/
│   │   ├── 001_initial_schema.sql
│   │   └── 002_market_data_and_strategy_tables.sql
│   └── tests/
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                # Dashboard overview
│   │   ├── portfolio/page.tsx      # Positions & margin
│   │   ├── recommendations/page.tsx # All signals + execute
│   │   ├── performance/page.tsx    # Equity curve & metrics
│   │   └── reports/page.tsx        # Daily reports archive
│   ├── components/
│   │   ├── PortfolioCard.tsx
│   │   ├── RecommendationCard.tsx
│   │   ├── EquityCurveChart.tsx
│   │   ├── RegimeIndicator.tsx
│   │   └── KillSwitch.tsx
│   └── lib/
│       ├── api.ts                  # Axios client
│       └── ws.ts                   # WebSocket client
└── docker-compose.yml
```

---

## Strategy Engine Pipeline

Runs at **09:15 IST** on trading days:

1. **Fetch Live Data** → Kite quotes/OHLC for watchlist symbols
2. **Detect Regime** → ADX, ATR, BB bandwidth, EMA slope
3. **Run 10 Strategies**:
   - EMA Crossover (trend)
   - Supertrend Follow (trend)
   - MACD Momentum (momentum)
   - Keltner Channel Trend (trend)
   - Donchian Breakout (breakout)
   - Bollinger Breakout (breakout)
   - VWAP Reversion (mean-reversion)
   - RSI Mean Reversion (mean-reversion)
   - Stochastic RSI Reversion (mean-reversion)
   - Multi-Timeframe Trend (trend)
4. **Rank Signals** → Confidence + historical perf + regime fit
5. **AI Summary** → OpenAI rationale, risks, invalidation
6. **Calculate Risk** → Position size, capital at risk, R:R
7. **Persist Recommendations** → Dashboard reads these

---

## Dashboard API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/portfolio` | Positions, capital, unrealized P&L |
| `GET /api/recommendations` | Pending recommendations with AI summary |
| `GET /api/regime` | Current regime per symbol |
| `POST /api/execute` | Manual execution of recommendation |
| `GET /api/performance` | Equity curve, strategy metrics, drawdown |
| `GET /api/daily-report` | Latest EOD report |
| `WS /ws/live` | Live P&L/position updates |

---

## Running Tests

```bash
cd backend
pytest tests/ -v
```

Tests cover:
- Regime detection (trending/range/volatile)
- Signal ranking (confidence + historical + regime)
- Risk engine extension (calculate_risk_for_signal)

---

## Production Deployment

### AWS ECS (Recommended)
1. Push images to ECR
2. Create ECS services for `app`, `frontend`, `postgres` (RDS), `redis` (ElastiCache)
3. Use Application Load Balancer
4. Configure secrets in AWS Secrets Manager

### Environment-Specific Configs
- Set `APP_ENV=production` in `.env`
- Use managed Postgres (RDS) and Redis (ElastiCache)
- Enable SSL/TLS termination at ALB
- Configure CloudWatch logging

---

## SEBI Compliance Features

- ✅ Algo-ID tagging on every order
- ✅ Immutable audit trail (audit_trail table)
- ✅ Daily token refresh at 08:45 IST
- ✅ Risk limits: daily P&L, trade count, cooldown
- ✅ Position monitoring with trailing SL
- ✅ Emergency exit endpoint (panic button)
- ✅ Daily report via Telegram

---

## License

Proprietary - SEBI-Compliant Algo Trading System