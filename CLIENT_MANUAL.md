# ALGO TRADE PRO — Client User Manual & Operating Guide
**SEBI-Compliant Algorithmic Trading Workstation (Version 2.0)**

---

## Table of Contents
1. [System Quickstart (1-Click Launch)](#1-system-quickstart-1-click-launch)
2. [Broker API & 2FA TOTP Onboarding](#2-broker-api--2fa-totp-onboarding)
3. [Trading Modes: Paper Demo vs. Live Real Capital](#3-trading-modes-paper-demo-vs-live-real-capital)
4. [Telegram Mobile Push Notifications](#4-telegram-mobile-push-notifications)
5. [SEBI Risk Limits & Circuit Breakers](#5-sebi-risk-limits--circuit-breakers)
6. [TradingView Signal Webhook Integration](#6-tradingview-signal-webhook-integration)
7. [Emergency Panic Square-Off (Kill Switch)](#7-emergency-panic-square-off-kill-switch)
8. [Automated Backups & Maintenance](#8-automated-backups--maintenance)

---

## 1. System Quickstart (1-Click Launch)

### Starting the System
1. Double-click the **`start_trading_system.bat`** file on your desktop.
2. The launcher will automatically start both the backend trading engine and the frontend workstation.
3. Your default web browser will open automatically to **[http://localhost:3000](http://localhost:3000)**.
4. Log in using your workstation credentials:
   - **Default User ID**: `DEV001` or `admin@trading.local`
   - **Default Password**: `trader@123` (editable in Settings)

### Stopping the System
- When trading concludes for the day (after 15:30 IST), double-click **`stop_trading_system.bat`** to safely stop all background daemons.

---

## 2. Broker API & 2FA TOTP Onboarding

To enable automated trade execution and sub-second live price streaming, link your broker credentials in **Risk & Settings** (`/settings`):

### Setting up Zerodha Kite Connect:
1. Log into your Kite developer portal at **[https://developers.kite.trade](https://developers.kite.trade)**.
2. Create a new app and copy your **API Key** and **API Secret**.
3. **Extracting your 2FA TOTP Secret Key** (Mandatory for automated 08:45 IST pre-market login):
   - Go to **Kite Web** $\rightarrow$ **My Profile** $\rightarrow$ **Password & Security**.
   - Click **Enable 2FA TOTP** (or Re-register TOTP).
   - Click **"Can't scan QR code? Copy TOTP Secret"**.
   - Copy the 32-character Base32 secret code (e.g. `JBSWY3DPEHPK3PXP...`).
4. Enter your **Kite User ID**, **API Key**, **API Secret**, **Password**, and **TOTP Secret** into the **Risk & Settings** portal.
5. Click **"Save & Authenticate Broker"**. The system will verify the connection and begin daily automatic token refreshes.

---

## 3. Trading Modes: Paper Demo vs. Live Real Capital

In the top header of the dashboard, you have an instant **Trading Mode Toggle**:

- 🟢 **`PAPER (DEMO)`**: 
  - Sub-second live market tick streaming and real-time P&L tracking.
  - Signal entries and exits are simulated with **₹0 financial risk**.
  - Recommended for strategy validation and backtesting verification.
- 🔴 **`LIVE (REAL ₹)`**:
  - Automatically routes orders to the exchange with exchange-assigned Algo-IDs.
  - Requires confirmation before activation to safeguard against accidental real-money orders.

---

## 4. Telegram Mobile Push Notifications

Receive real-time push alerts on your phone whenever trades are placed, stop-losses are trailed, or daily loss limits are approached:

1. Open Telegram and search for **`@BotFather`** $\rightarrow$ create a new bot and copy your **Bot Token**.
2. Search for **`@userinfobot`** $\rightarrow$ obtain your personal numeric **Chat ID**.
3. In **Risk & Settings** (`/settings`), enter your **Bot Token** and **Chat ID**.
4. Click **"Send Test Mobile Alert"** to confirm instant message delivery.

---

## 5. SEBI Risk Limits & Circuit Breakers

The system features real-time regulatory risk engine guards that prevent rogue trades:

- **Max Daily Loss Limit (₹)**: If your account's net realized + unrealized loss reaches this threshold, all automated buying is halted immediately.
- **Risk Per Trade (%)**: Calculates dynamic position sizing so no single trade risks more than your configured capital percentage.
- **Max Trades Per Day**: Enforces a hard daily execution quota.
- **Cooldown After Loss**: Prevents revenge-trading by enforcing a 20-minute pause following a losing trade.
- **Dynamic Trailing Stop-Loss**: Automatically locks in profits as the market moves in your favor.

---

## 6. TradingView Signal Webhook Integration

To connect custom TradingView indicator alerts to the system:

1. Set your TradingView alert Webhook URL to:
   ```text
   http://YOUR_SERVER_IP:8000/webhook/tradingview
   ```
2. Set the alert message JSON body:
   ```json
   {
     "secret": "YOUR_WEBHOOK_SECRET",
     "webhook_token": "YOUR_STRATEGY_TOKEN",
     "symbol": "RELIANCE",
     "exchange": "NSE",
     "direction": "BUY",
     "price": 2500.0,
     "quantity": 10
   }
   ```

---

## 7. Emergency Panic Square-Off (Kill Switch)

In the event of unexpected market volatility or exchange outages:
1. Click the red **`EMERGENCY EXIT`** button in the top right header.
2. Select **"Panic Exit All Open Positions"** and confirm.
3. The system will instantaneously cancel all pending orders and place market square-off orders across all open legs.

---

## 8. Automated Backups & Maintenance

- **Daily Database Snapshots**: An automated SQLite backup is created at 16:00 IST every trading day and stored in the `backups/` folder.
- **Log Rotation**: Application logs are automatically rotated with a 50 MB limit and 30-day retention in `logs/trading_system.log`.

---
*SEBI Compliance Notice: Algorithmic trading involves financial risk. Ensure that all API credentials and risk limits are verified before enabling live broker execution.*
