# Axodus Trading Suite - Arquitetura Técnica e Contratos de API

**Data**: Abril 2026  
**Versão**: 1.0  
**Propósito**: Referência técnica detalhada. Schemas exatos, contratos de API, padrões de erro, fluxos de dados.

---

## 📐 Arquitetura de Dados

### Fluxo de Dados End-to-End

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AGENTE EXTERNO                                 │
│              (Trinity, UI, Automação, CLI)                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ↓                 ↓                 ↓
    ┌─────────┐       ┌──────────┐       ┌──────────┐
    │ Quants- │       │Hummingbot│       │ Condor   │
    │  Lab    │       │   API    │       │  API     │
    │(8075)   │       │ (8000)   │       │ (8088)   │
    └────┬────┘       └────┬─────┘       └────┬─────┘
         │                 │                   │
         └─────────────────┼───────────────────┘
                           ↓
    ┌─────────────────────────────────────────┐
    │   Camada de Persistência                │
    ├─────────────────────────────────────────┤
    │ PostgreSQL (account_states, configs)    │
    │ MongoDB (estudos, caches, market data)  │
    │ EMQX (streaming, pub/sub)               │
    │ Redis (opcional - rate limit, cache)    │
    └─────────────────────────────────────────┘
         │
         ↓
    ┌─────────────────────────────────────────┐
    │   Camada de Trading Runtime             │
    ├─────────────────────────────────────────┤
    │ • Controllers (strategies)               │
    │ • Executores (orders, positions)         │
    │ • Market data feeds                     │
    └─────────────────────────────────────────┘
         │
         ↓
    ┌─────────────────────────────────────────┐
    │   Camada de Exchanges                   │
    ├─────────────────────────────────────────┤
    │ CEX: Binance, Bybit, OKX, ...          │
    │ DEX: Uniswap, Jupiter, Serum, ...      │
    │ Data: CoinGecko, Binance Stream, ...   │
    └─────────────────────────────────────────┘
```

### Tabelas e Coleções

#### PostgreSQL (Hummingbot)

```sql
-- Accounts e Credenciais
TABLE accounts {
  account_id UUID PRIMARY KEY,
  account_name VARCHAR,
  created_at TIMESTAMP,
  is_active BOOLEAN
}

TABLE account_states {
  account_id UUID,
  connector_name VARCHAR,
  account_state JSONB,  -- {balances: {...}, positions: [...]}
  updated_at TIMESTAMP
}

TABLE exchange_credentials {
  credential_id UUID PRIMARY KEY,
  account_id UUID,
  connector_name VARCHAR,
  encrypted_keys BYTEA,  -- Credenciais criptografadas
  created_at TIMESTAMP
}

-- Market Data Cache
TABLE market_data {
  id UUID PRIMARY KEY,
  connector VARCHAR,
  trading_pair VARCHAR,
  candle_size VARCHAR,
  timestamp TIMESTAMP,
  open NUMERIC,
  high NUMERIC,
  low NUMERIC,
  close NUMERIC,
  volume NUMERIC,
  INDEX (connector, trading_pair, candle_size, timestamp)
}

-- Orders e Trades
TABLE orders {
  order_id VARCHAR PRIMARY KEY,
  account_id UUID,
  connector VARCHAR,
  trading_pair VARCHAR,
  side VARCHAR,  -- BUY, SELL
  order_type VARCHAR,  -- MARKET, LIMIT
  amount NUMERIC,
  price NUMERIC,
  status VARCHAR,  -- OPEN, FILLED, CANCELED, FAILED
  created_at TIMESTAMP,
  updated_at TIMESTAMP
}

TABLE trades {
  trade_id VARCHAR PRIMARY KEY,
  order_id VARCHAR,
  filled_amount NUMERIC,
  filled_price NUMERIC,
  fee NUMERIC,
  executed_at TIMESTAMP
}
```

#### MongoDB (Quants-Lab)

```javascript
// Estudos (Studies)
db.studies.insertOne({
  _id: ObjectId(),
  study_name: "my_study_20240427",
  controller: "macd_bb_v1",
  controller_type: "directional",
  status: "COMPLETED",  // CREATED, VALIDATING, RUNNING, COMPLETED, FAILED
  manifest: { /* full manifest */ },
  
  // Resultados
  optimization_results: {
    best_params: { /* params */ },
    best_value: 2.15,
    n_trials: 100,
    trials_data: [/* Trial records */]
  },
  
  backtesting_results: {
    start_date: "2024-01-01",
    end_date: "2024-03-31",
    performance: {
      total_return: 0.153,
      sharpe_ratio: 2.15,
      max_drawdown: -0.042,
      win_rate: 0.625,
      num_trades: 47
    },
    trades: [/* Trade records */],
    equity_curve: [/* Daily equity */)
  },
  
  created_at: ISODate(),
  updated_at: ISODate()
})

// Approval Requests
db.approval_requests.insertOne({
  _id: ObjectId(),
  approval_id: "appr-20240427-xyz789",
  study_name: "my_study_20240427",
  status: "PENDING",  // PENDING, APPROVED, REJECTED
  created_at: ISODate(),
  expires_at: ISODate("2024-04-29"),
  
  decision: {
    action: "APPROVED",  // preenchido após decisão
    decision_maker: "trader_admin",
    notes: "Approved for live trading",
    decided_at: ISODate()
  }
})

// Market Data Cache (para backtests rápidos)
db.market_data_cache.insertOne({
  _id: ObjectId(),
  connector: "binance",
  trading_pair: "BTC-USDT",
  candle_size: "1h",
  date: ISODate("2024-01-01"),
  
  candles: [
    { timestamp: 1234567890, o: 40000, h: 41000, l: 39500, c: 40500, v: 100 },
    // ...
  ],
  
  last_update: ISODate()
})
```

#### EMQX (Pub/Sub Topics)

```
# Trinity → Suite
trinity/commands/+                      # Trinity publica comandos
trinity/heartbeat                       # Trinity publica heartbeat

# Hummingbot → Suite
hummingbot/accounts/+/state             # Estado de account
hummingbot/orders/+/new                 # Nova ordem
hummingbot/trades/+/executed            # Trade executado
hummingbot/positions/+/update           # Posição atualizada
hummingbot/data/market/+/+/candle       # Candle atualizado

# Quants-Lab → Suite
quants/jobs/+/status                    # Job status update
quants/approvals/new                    # Nova approval request

# Condor → Suite
condor/users/+/action                   # Ação via Telegram
```

---

## 🔌 Contratos de API Detalhados

### Hummingbot API

**Base URL**: `http://localhost:8000`  
**Autenticação**: HTTP Basic Auth (username:password)

#### Health Check

```http
GET /health

Response 200:
{
  "status": "ok",
  "timestamp": "2024-04-27T10:15:00Z",
  "version": "1.0.0"
}
```

#### Accounts Management

```http
GET /accounts/
Authorization: Basic <base64>

Response 200:
{
  "accounts": [
    {
      "account_id": "uuid-123",
      "account_name": "master_account",
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}

---

POST /accounts/add-account
Authorization: Basic <base64>
Content-Type: application/json

{
  "account_name": "trading_account_01"
}

Response 201:
{
  "account_id": "uuid-456",
  "account_name": "trading_account_01",
  "is_active": true
}
```

#### Connectors and Credentials

```http
GET /connectors/
Authorization: Basic <base64>

Response 200:
{
  "connectors": [
    {
      "connector_name": "binance",
      "type": "CEX",
      "supported_trading_types": ["SPOT", "PERPETUAL"],
      "config_keys": ["binance_api_key", "binance_api_secret"],
      "is_sandbox_available": true
    },
    {
      "connector_name": "uniswap",
      "type": "DEX",
      "supported_trading_types": ["SWAP"],
      "config_keys": ["wallet_address", "rpc_url"]
    }
  ]
}

---

POST /accounts/add-credential/{account_name}/{connector_name}
Authorization: Basic <base64>
Content-Type: application/json

{
  "binance_api_key": "...",
  "binance_api_secret": "...",
  "binance_testnet": false
}

Response 201:
{
  "credential_id": "uuid-789",
  "account_name": "master_account",
  "connector_name": "binance",
  "created_at": "2024-04-27T10:15:00Z"
}
```

#### Portfolio State

```http
POST /portfolio/state
Authorization: Basic <base64>
Content-Type: application/json

{
  "account_names": ["master_account"],
  "connector_names": ["binance", "uniswap"]
}

Response 200:
{
  "timestamp": "2024-04-27T10:15:00Z",
  "accounts": [
    {
      "account_name": "master_account",
      "balances": {
        "binance": {
          "BTC": {
            "total": 1.5,
            "available": 1.0,
            "reserved": 0.5,
            "usd_value": 65000
          },
          "USDT": {
            "total": 50000,
            "available": 50000,
            "reserved": 0,
            "usd_value": 50000
          }
        },
        "uniswap": {
          "ETH": { "total": 5.0, "usd_value": 18000 },
          "USDC": { "total": 10000, "usd_value": 10000 }
        }
      },
      "total_portfolio_usd": 143000,
      "positions": [
        {
          "connector": "binance_perpetual",
          "trading_pair": "BTC-USDT",
          "side": "LONG",
          "amount": 0.5,
          "entry_price": 40000,
          "current_price": 43000,
          "unrealized_pnl": 1500,
          "leverage": 5
        }
      ],
      "orders": [
        {
          "order_id": "ord-001",
          "connector": "binance",
          "trading_pair": "BTC-USDT",
          "side": "BUY",
          "amount": 0.1,
          "price": 42000,
          "status": "OPEN",
          "created_at": "2024-04-27T10:00:00Z"
        }
      ]
    }
  ],
  "portfolio_summary": {
    "total_balance_usd": 143000,
    "total_unrealized_pnl": 1500,
    "24h_change_pct": 2.3
  }
}
```

#### Market Data

```http
POST /market-data/candles
Authorization: Basic <base64>
Content-Type: application/json

{
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "interval": "1h",
  "max_records": 100,
  "start_time": "2024-01-01T00:00:00Z",
  "end_time": "2024-04-27T10:15:00Z"
}

Response 200:
{
  "connector": "binance",
  "trading_pair": "BTC-USDT",
  "interval": "1h",
  "candles": [
    {
      "timestamp": 1704067200,
      "open": 40000.50,
      "high": 41000.00,
      "low": 39500.25,
      "close": 40500.75,
      "volume": 125.5,
      "quote_asset_volume": 5150237.50
    },
    // ... more candles
  ],
  "total": 2184
}
```

#### Place Order

```http
POST /trading/orders
Authorization: Basic <base64>
Content-Type: application/json

{
  "account_name": "master_account",
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "order_type": "LIMIT",
  "side": "BUY",
  "amount": 0.05,
  "price": 42000.0,
  "time_in_force": "GTC"  // GTC, IOC, PO
}

Response 201:
{
  "order_id": "ord-abc123",
  "account_name": "master_account",
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "order_type": "LIMIT",
  "side": "BUY",
  "amount": 0.05,
  "price": 42000.0,
  "status": "OPEN",
  "created_at": "2024-04-27T10:15:00Z",
  "fills": []
}
```

---

### Quants-Lab API

**Base URL**: `http://localhost:8075`  
**Autenticação**: Nenhuma (executado localmente) ou API Key (se exposto)

#### Health Check

```http
GET /health

Response 200:
{
  "status": "ok",
  "database": "connected",
  "timestamp": "2024-04-27T10:15:00Z"
}
```

#### Validate Strategy

```http
POST /api/v1/strategies/validate
Content-Type: application/json

{
  "study_name": "test_validation",
  "controller": "macd_bb_v1",
  "controller_type": "directional",
  "start_date": "2024-01-01",
  "end_date": "2024-03-31",
  "trading_pair": "BTC-USDT",
  "connector": "binance",
  "candle_size": "1h",
  "initial_portfolio": {
    "BTC": 1.0,
    "USDT": 10000.0
  },
  "filters": {
    "max_loss_pct": -5.0,
    "min_sharpe_ratio": 1.0
  },
  "parameter_ranges": {
    "macd_fast_period": {"min": 6, "max": 14},
    "macd_slow_period": {"min": 20, "max": 32},
    "bb_period": {"min": 15, "max": 25}
  },
  "fixed_parameters": {
    "grid_levels": 5,
    "take_profit_ratio": 0.02
  }
}

Response 200:
{
  "valid": true,
  "study_name": "test_validation",
  "warnings": [],
  "validation_rules": {
    "controller_exists": true,
    "date_range_valid": true,
    "initial_portfolio_valid": true,
    "parameter_ranges_valid": true,
    "filters_sensible": true
  }
}

---

Response 422 (Invalid):
{
  "valid": false,
  "errors": [
    "Controller 'invalid_controller' not found",
    "Start date must be after 2020-01-01"
  ],
  "warnings": [
    "Sharpe ratio filter of 2.0 is very restrictive"
  ]
}
```

#### Backtest

```http
POST /api/v1/backtests
Content-Type: application/json

{
  "manifest": {
    /* full manifest from validate endpoint */
  },
  "candidate_params": {
    "macd_fast_period": 12,
    "macd_slow_period": 26,
    "bb_period": 20,
    "grid_levels": 5,
    "take_profit_ratio": 0.02
  },
  "export_artifacts": true
}

Response 202 (Accepted):
{
  "job_id": "bt-20240427-abc123xyz",
  "study_name": "my_study_20240427",
  "status": "QUEUED",
  "created_at": "2024-04-27T10:15:00Z",
  "estimated_duration_seconds": 300
}
```

#### Optimization

```http
POST /api/v1/optimizations
Content-Type: application/json

{
  "manifest": {
    /* full manifest */
  },
  "optimization_config": {
    "direction": "maximize",  // minimize, maximize
    "metric": "sharpe_ratio",  // total_return, sharpe_ratio, max_drawdown, win_rate
    "n_trials": 100,
    "timeout_minutes": 120,
    "sampler": "tpe",  // tpe (default), random, grid
    "pruner": "median"  // median, hyperband, noop
  }
}

Response 202 (Accepted):
{
  "job_id": "opt-20240427-def456uvw",
  "study_name": "my_study_20240427",
  "status": "QUEUED",
  "created_at": "2024-04-27T10:15:00Z",
  "estimated_duration_seconds": 3600
}
```

#### Poll Job Status

```http
GET /api/v1/jobs/{job_id}

Response 200:
{
  "job_id": "opt-20240427-def456uvw",
  "study_name": "my_study_20240427",
  "type": "optimization",  // backtest, optimization
  "status": "RUNNING",  // QUEUED, RUNNING, COMPLETED, FAILED, CANCELED
  "progress": {
    "current_trial": 42,
    "total_trials": 100,
    "percentage": 42.0,
    "elapsed_seconds": 1850,
    "estimated_remaining_seconds": 2450
  },
  "created_at": "2024-04-27T10:15:00Z",
  "started_at": "2024-04-27T10:20:00Z",
  "completed_at": null,
  
  // Preenchido ao finalizar
  "result": {
    "best_params": { /* params */ },
    "best_value": 2.15,
    "n_trials_completed": 42
  }
}
```

#### Get Study Details

```http
GET /api/v1/studies/{study_name}

Response 200:
{
  "study_name": "my_study_20240427",
  "controller": "macd_bb_v1",
  "status": "COMPLETED",
  "manifest": { /* full manifest */ },
  
  "backtesting_results": {
    "start_date": "2024-01-01",
    "end_date": "2024-03-31",
    "total_days": 90,
    "num_trades": 47,
    "performance": {
      "total_return": 0.153,
      "annual_return": 0.612,
      "sharpe_ratio": 2.15,
      "sortino_ratio": 3.42,
      "max_drawdown": -0.042,
      "recovery_factor": 3.64,
      "win_rate": 0.625,
      "profit_factor": 2.3
    },
    "trades_summary": [
      {
        "trade_id": "trd-001",
        "entry_time": "2024-01-05T10:00:00Z",
        "entry_price": 40000,
        "exit_time": "2024-01-05T14:00:00Z",
        "exit_price": 40500,
        "pnl": 500,
        "pnl_pct": 0.0125,
        "duration_hours": 4
      }
    ]
  },
  
  "optimization_results": {
    "n_trials": 100,
    "best_params": { /* params */ },
    "best_value": 2.15,
    "direction": "maximize",
    "metric": "sharpe_ratio"
  },
  
  "created_at": "2024-04-27T10:15:00Z",
  "updated_at": "2024-04-27T11:45:00Z"
}
```

#### Get Report

```http
GET /api/v1/reports/{study_name}

Response 200:
{
  "study_name": "my_study_20240427",
  "report_version": "1.0",
  "generated_at": "2024-04-27T11:45:00Z",
  
  "summary": {
    "total_return": "15.3%",
    "sharpe_ratio": 2.15,
    "max_drawdown": "-4.2%",
    "win_rate": "62.5%",
    "num_trades": 47,
    "trading_duration": "Q1 2024"
  },
  
  "performance_breakdown": {
    "by_month": [
      {
        "month": "2024-01",
        "return_pct": 5.2,
        "sharpe": 1.8,
        "trades": 15
      }
    ]
  },
  
  "top_trades": [
    {
      "rank": 1,
      "pnl": 2150,
      "entry": "2024-01-15 10:00",
      "exit": "2024-01-15 14:30"
    }
  ],
  
  "artifacts": {
    "html_report_url": "/api/v1/reports/my_study_20240427/report.html",
    "equity_curve_url": "/api/v1/reports/my_study_20240427/equity_curve.png",
    "human_report_url": "/api/v1/reports/my_study_20240427/human_report.md",
    "trades_csv_url": "/api/v1/reports/my_study_20240427/trades.csv",
    "metrics_json_url": "/api/v1/reports/my_study_20240427/metrics.json"
  },
  
  "approval_request": {
    "approval_id": "appr-20240427-xyz789",
    "status": "PENDING",
    "created_at": "2024-04-27T11:45:00Z",
    "expires_at": "2024-04-29T11:45:00Z",
    "messages": [
      {
        "level": "INFO",
        "text": "Strategy passed all validation rules"
      }
    ]
  }
}
```

#### Approvals Management

```http
GET /api/v1/approvals?study_name=my_study_20240427

Response 200:
{
  "approvals": [
    {
      "approval_id": "appr-20240427-xyz789",
      "study_name": "my_study_20240427",
      "status": "PENDING",
      "created_at": "2024-04-27T11:45:00Z",
      "expires_at": "2024-04-29T11:45:00Z",
      "context": {
        "performance": { /* metrics */ },
        "risks": ["High leverage", "Correlated pairs"],
        "recommendations": ["Start with 10% position size"]
      }
    }
  ]
}

---

POST /api/v1/approvals/{approval_id}
Content-Type: application/json

{
  "action": "APPROVE",  // APPROVE, REJECT
  "decision_maker": "trader_admin",
  "notes": "Approved for live trading. Start with 10% position size."
}

Response 200:
{
  "approval_id": "appr-20240427-xyz789",
  "study_name": "my_study_20240427",
  "status": "APPROVED",
  "decision": {
    "action": "APPROVE",
    "decision_maker": "trader_admin",
    "notes": "Approved for live trading. Start with 10% position size.",
    "decided_at": "2024-04-27T12:00:00Z"
  }
}
```

---

### Condor API

**Base URL**: `http://localhost:8088`  
**Autenticação**: Bearer token (JWT ou CONDOR_TOKEN)

#### Health Check

```http
GET /health

Response 200:
{
  "status": "ok",
  "version": "1.0.0",
  "connectedToHummingbot": true
}
```

#### Portfolio Overview

```http
GET /api/v1/portfolio
Authorization: Bearer <token>

Response 200:
{
  "total_balance_usd": 143000,
  "change_24h_pct": 2.3,
  "change_7d_pct": 5.1,
  "accounts": [
    {
      "account_name": "master_account",
      "balances": {
        "BTC": { "amount": 1.5, "usd_value": 65000 },
        "USDT": { "amount": 50000, "usd_value": 50000 }
      },
      "total_usd": 115000,
      "positions": [
        {
          "connector": "binance_perpetual",
          "pair": "BTC-USDT",
          "side": "LONG",
          "amount": 0.5,
          "entry_price": 40000,
          "current_price": 43000,
          "unrealized_pnl": 1500
        }
      ]
    }
  ]
}
```

#### Bots Management

```http
GET /api/v1/bots
Authorization: Bearer <token>

Response 200:
{
  "bots": [
    {
      "bot_id": "bot-001",
      "instance_name": "paper_bot_001",
      "account": "master_account",
      "controller": "macd_bb_v1",
      "status": "RUNNING",
      "trading_pair": "BTC-USDT",
      "uptime_hours": 24.5,
      "performance": {
        "pnl": 1250.50,
        "pnl_pct": 2.3,
        "trades_executed": 12
      },
      "last_activity": "2024-04-27T10:15:00Z"
    }
  ]
}
```

---

### MCP Hummingbot

**Transporte**: stdio (padrão) ou HTTP (opcional)

#### Tool: configure_api_servers

```python
configure_api_servers(
    action="add",  # add, delete, set_default, list
    name="local",
    host="localhost",
    port=8000,
    username="admin",
    password="admin"
)

# Returns:
{
  "status": "success",
  "server": {
    "name": "local",
    "host": "localhost",
    "port": 8000,
    "is_default": True
  }
}
```

#### Tool: get_portfolio_overview

```python
get_portfolio_overview(
    account_names=["master_account"],
    connector_names=["binance"],
    include_balances=True,
    include_perp_positions=True,
    include_lp_positions=True,
    include_active_orders=True,
    as_distribution=False
)

# Returns portfolio JSON (mesmo schema de /portfolio/state)
```

#### Tool: place_order

```python
place_order(
    connector_name="binance",
    trading_pair="BTC-USDT",
    trade_type="BUY",
    amount="0.05",  # ou "$100" para USD
    order_type="LIMIT",
    price="42000",
    account_name="master_account"
)

# Returns:
{
  "order_id": "ord-abc123",
  "status": "OPEN",
  "connector": "binance",
  "pair": "BTC-USDT",
  "amount": 0.05,
  "price": 42000.0
}
```

---

## 🔄 Sequências de Integração

### Sequência 1: Trinity → Validate → Backtest → Report

```
Trinity                    Quants-Lab                MongoDB
  │                            │                        │
  │─ POST /validate ──────────→│                        │
  │                            │─ verify manifest ─────→│
  │                            │←─ manifest_valid ──────│
  │← {"valid": true} ──────────│                        │
  │                            │                        │
  │─ POST /backtests ─────────→│                        │
  │                            │─ queue job ───────────→│
  │← {"job_id": "..."} ────────│                        │
  │                            │                        │
  │─ GET /jobs/{job_id} ──────→│  [RUNNING] (3 vezes)  │
  │← {"status": "RUNNING"} ────│                        │
  │                            │                        │
  │─ GET /jobs/{job_id} ──────→│                        │
  │← {"status": "COMPLETED"} ──│                        │
  │                            │                        │
  │─ GET /reports/{study} ────→│─ fetch results ──────→│
  │← {"equity_curve": ...} ────│←─ performance_data ───│
```

### Sequência 2: Trinity → Optimize → Poll → Approve → Deploy

```
Trinity              Quants-Lab         Hummingbot API
  │                      │                    │
  │─ POST /optimize ────→│                    │
  │← {"job_id"} ────────│                    │
  │                      │ [Optuna running 30-120 min]
  │ [wait 60s]           │
  │─ GET /jobs/... ─────→│                    │
  │← {"status": "RUNNING", "progress": "42/100"}
  │                      │
  │ [wait 300s, poll again]
  │─ GET /jobs/... ─────→│                    │
  │← {"status": "COMPLETED"}
  │                      │
  │─ GET /reports/... ──→│                    │
  │← {"approval_request": {"status": "PENDING"}}
  │                      │
  │─ POST /approvals/{id}→│                    │
  │← {"status": "APPROVED"}
  │                      │
  │─ deploy via API ─────────────────────────→│
  │                      │                    │
  │                      │              [executor created]
```

---

## ⚙️ Padrões de Erro

### HTTP Status Codes

```
200 OK                  ✓ Sucesso
201 Created             ✓ Recurso criado
202 Accepted            ✓ Job aceito (async)
400 Bad Request         ✗ Payload inválido
401 Unauthorized        ✗ Credenciais inválidas
404 Not Found           ✗ Recurso não encontrado
422 Unprocessable       ✗ Lógica inválida (validação)
500 Server Error        ✗ Erro interno
503 Service Unavailable ✗ Serviço indisponível
```

### Error Response Schema

```json
{
  "error": {
    "code": "INVALID_MANIFEST",
    "message": "Strategy manifest failed validation",
    "details": {
      "field": "controller",
      "issue": "Controller 'invalid_name' not found"
    },
    "request_id": "req-20240427-abc123"
  }
}
```

### Retry Strategy

```
Status  | Retry? | Delay   | Max
--------|--------|---------|-----
200-299 | No     | -       | -
4xx     | No*    | -       | - (*except 429)
429     | Yes    | 60s     | 3x
5xx     | Yes    | 10s     | 5x
Timeout | Yes    | 30s     | 3x
```

---

## 📊 Schemas Principais

### StrategyManifest

```typescript
interface StrategyManifest {
  // Identidade
  study_name: string;              // Único por rodada
  controller: string;              // "macd_bb_v1", etc.
  controller_type: string;         // "directional", "mean_reversion", etc.

  // Dados
  connector: string;               // "binance", "kucoin", etc.
  trading_pair: string;            // "BTC-USDT", "ETH-USDC", etc.
  candle_size: string;             // "1m", "5m", "1h", "1d"
  start_date: string;              // ISO8601
  end_date: string;

  // Portfolio inicial
  initial_portfolio: {
    [symbol: string]: number;
  };

  // Ranges de otimização
  parameter_ranges: {
    [param: string]: {
      min: number;
      max: number;
      type?: "int" | "float";  // default: float
      step?: number;           // para int
    };
  };

  // Parâmetros fixos (não otimizáveis)
  fixed_parameters?: {
    [param: string]: any;
  };

  // Filtros de validação
  filters?: {
    max_loss_pct?: number;        // e.g., -5.0
    min_sharpe_ratio?: number;    // e.g., 1.0
    min_win_rate?: number;        // e.g., 0.5
    max_drawdown_pct?: number;    // e.g., -10.0
  };
}
```

### JobRecord

```typescript
interface JobRecord {
  job_id: string;
  study_name: string;
  type: "backtest" | "optimization";
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELED";
  
  created_at: ISO8601;
  started_at?: ISO8601;
  completed_at?: ISO8601;
  
  progress?: {
    current_trial: number;
    total_trials: number;
    percentage: number;
    elapsed_seconds: number;
    estimated_remaining_seconds: number;
  };
  
  result?: {
    best_params: Record<string, number>;
    best_value: number;
    n_trials_completed: number;
  };
  
  error?: {
    code: string;
    message: string;
    details: any;
  };
}
```

### BacktestingResults

```typescript
interface BacktestingResults {
  start_date: string;
  end_date: string;
  total_days: number;
  
  performance: {
    total_return: number;           // 0.153 = 15.3%
    annual_return: number;
    sharpe_ratio: number;
    sortino_ratio: number;
    max_drawdown: number;           // -0.042 = -4.2%
    recovery_factor: number;
    win_rate: number;               // 0.625 = 62.5%
    profit_factor: number;          // win_sum / loss_sum
    trades_count: number;
  };
  
  trades: Array<{
    trade_id: string;
    entry_time: ISO8601;
    entry_price: number;
    exit_time: ISO8601;
    exit_price: number;
    pnl: number;
    pnl_pct: number;
    duration_hours: number;
  }>;
  
  equity_curve: Array<{
    timestamp: ISO8601;
    equity: number;
  }>;
}
```

---

## 🔐 Padrões de Segurança

### Criptografia de Credenciais

```
User Input (plaintext)
  ↓
[Encryption with master key]
  ↓
Database (encrypted)
  ↓
[At runtime: decrypt on demand]
  ↓
API Client (plaintext in memory only)
```

### JWT Token

```json
{
  "header": {
    "alg": "HS256",
    "typ": "JWT"
  },
  "payload": {
    "sub": "user_id",
    "iat": 1704067200,
    "exp": 1704153600,  // 24h
    "scope": ["trading", "portfolio_read"]
  },
  "signature": "..."
}
```

---

**Versão**: 1.0  
**Última Atualização**: 27/04/2026
