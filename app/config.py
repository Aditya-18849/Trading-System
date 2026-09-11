"""
Centralized configuration. All secrets/tunables come from environment
variables (.env in dev, injected via ECS/EC2 env or AWS Secrets Manager in
prod). Never hardcode credentials in code.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None

    # Webhook
    tradingview_webhook_secret: str

    # API Authentication
    admin_api_key: str = ""  # Required for /admin/* and /reports/* endpoints
    allowed_webhook_ips: str | None = None  # Comma-separated TradingView IP whitelist

    # Broker (Zerodha Kite Connect)
    kite_api_key: str
    kite_api_secret: str
    kite_user_id: str
    kite_password: str
    kite_totp_secret: str
    kite_algo_id: str  # exchange-assigned Algo-ID / strategy tag from broker

    # Broker (Angel One SmartAPI) — optional second broker
    angel_api_key: str | None = None
    angel_client_id: str | None = None
    angel_password: str | None = None
    angel_totp_secret: str | None = None
    angel_algo_id: str | None = None

    # Risk management defaults (overridable per-user in DB)
    total_capital: float = 100000
    risk_per_trade_pct: float = 1.0
    max_daily_loss: float = 1000
    max_trades_per_day: int = 3
    cooldown_minutes_after_loss: int = 20
    default_stoploss_pct: float = 0.5
    default_target_pct: float = 1.0

    # Position Monitor (trailing stop-loss & real-time monitoring)
    trailing_sl_pct: float = 0.3                   # Trail SL by this % of LTP
    trailing_sl_activation_pct: float = 0.3         # Activate trailing after this % favorable move
    monitor_poll_interval_sec: int = 5              # REST polling fallback interval (seconds)
    monitor_pnl_check_interval_sec: int = 10        # Daily P&L circuit-breaker check interval

    # WebSocket reconnect
    ws_reconnect_max_retries: int = 5              # Max reconnection attempts before giving up
    ws_reconnect_base_delay_sec: float = 2.0       # Base delay for exponential backoff (2→4→8→16→32s)

    # Heartbeat / health reporting
    heartbeat_interval_sec: int = 60               # Log heartbeat every N seconds
    heartbeat_telegram_every_n: int = 10            # Send Telegram heartbeat every Nth beat

    # Auto square-off
    auto_square_off_time: str = "15:15"             # IST time (HH:MM) to auto-exit all MIS positions

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Rate Limiting
    rate_limit: str = "30/minute"

    # CORS
    cors_origins: str = "*"

    # Operating Mode
    paper_trading_mode: bool = True  # True: Paper trading (simulated fills), False: Live broker execution
    jwt_secret_key: str = "algo-trading-jwt-secret-key-sebi-v2"

    app_env: str = "production"


settings = Settings()
