# Axodus Trading Suite - Guia Operacional Unificado

**Data de Criação**: Abril 2026  
**Versão**: 1.0  
**Propósito**: Documento de referência unificado para toda a Axodus Trading Suite. Consolida integração, operação e procedimentos para agents, operadores e desenvolvedores.

---

## 📋 Sumário Executivo

A Axodus Trading Suite é um ecossistema integrado de backtesting, otimização, orquestração de trading e monitoramento. Composto por:

| Componente | Papel | Status |
|---|---|---|
| **Hummingbot API** | Runtime e execução de trading | ✅ Operacional |
| **Quants-Lab** | Motor de backtesting, otimização e relatórios | ✅ Operacional |
| **Condor** | Interface Telegram para monitoramento e trading | ✅ Operacional |
| **MCP Hummingbot** | Protocolo de Contexto de Modelo para AI/agents | ✅ Operacional |
| **OpenClaw Trinity** | Agente orquestrador e criação de estratégias | 🔶 Pronto para integração |
| **Axodus Web UI** | Interface comercial (futuro) | 📋 Planejado |

**Fluxo Simplificado**:
```
Trinity (Agent)
    ↓
Quants-Lab (Estratégia + Backtest + Aprovação)
    ↓
Hummingbot API (Deploy + Execução)
    ↓
Condor (Monitoramento + Operação)
    ↓
Market Data → Feedback → Trinity (Loop)
```

---

## 🏗️ Arquitetura

### Serviços Core

```
┌─────────────────────────────────────────────────────────────────┐
│                        Camada de Agentes                         │
│  OpenClaw Trinity • Axodus Web UI • Automações Externas          │
└──────────┬───────────────┬────────────────┬──────────────────────┘
           │               │                │
           ↓               ↓                ↓
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  Quants-Lab API  │ │ Hummingbot API   │ │  Condor API      │
│  (porta 8075)    │ │ (porta 8000)     │ │ (porta 8088)     │
└──────────┬───────┘ └────────┬─────────┘ └────────┬─────────┘
           │                  │                    │
           └──────────────────┼────────────────────┘
                              ↓
        ┌────────────────────────────────────────┐
        │    Camada de Infraestrutura            │
        ├────────────────────────────────────────┤
        │ • PostgreSQL (account_states, etc)     │
        │ • MongoDB (estudos, caches, dados)     │
        │ • EMQX (mensagens, eventos)            │
        │ • MCP Server (AI/agent tools)          │
        └────────────────────────────────────────┘
                              ↓
        ┌────────────────────────────────────────┐
        │    Camada de Trading Runtime           │
        ├────────────────────────────────────────┤
        │ • Controllers (v2 code-based)          │
        │ • Executores (CEX, DEX, LP)            │
        │ • Market Data (feeds, candles)         │
        │ • Orders + Positions                   │
        └────────────────────────────────────────┘
```

### Redes Docker

```
axodus_backend_network
  ├── hummingbot-api
  ├── hummingbot-postgres
  └── hummingbot-gateway (opcional)

axodus_emqx_network
  ├── emqx
  └── (subscribers: hummingbot-api, condor, trinity externo)

axodus_mcp_network
  ├── mcp-hummingbot
  └── (clients: claude code, gemini, externos)
```

---

## 🚀 Setup Completo

### Pré-requisitos

- Docker + Docker Compose
- Python 3.11+ (para Quants-Lab, Condor, Trinity externo)
- 8GB RAM mínimo (recomendado: 16GB)
- Variáveis de ambiente configuradas

### Passo 1: Ambiente

```bash
# Clone repositórios
cd /opt
git clone https://github.com/Axodus/hummingbot-api.git
git clone https://github.com/Axodus/quants-lab.git
git clone https://github.com/Axodus/condor.git
git clone https://github.com/Axodus/mcp-hummingbot.git mcp-hummingbot
git clone -b master https://github.com/Axodus/Tradingbot.git

# Preencha variáveis de ambiente
cd /opt/hummingbot-api
cp .env.example .env.postgres
cp .env.example .env.mqtt
cp .env.example .env.hummingbot
cp .env.example .env.mcp
cp .env.example .env.condor
cp .env.example .env.trinity  # Para Trinity externo

# Edite os arquivos conforme necessário
```

### Passo 2: Bootstrap da Stack

```bash
cd /opt/hummingbot-api

# Validar pré-requisitos
./scripts/validate_prerequisites.sh

# Bootstrap automático
./scripts/bootstrap_stack.sh

# Verifica saúde
./scripts/healthcheck.sh
```

### Passo 3: Validar Integrações

```bash
# Testa conectividade entre componentes
./scripts/validate_integrations.sh

# Saída esperada:
# ✓ API autenticada consegue listar connectors
# ✓ API e PostgreSQL compartilham schema
# ✓ MCP consegue alcançar a API
# ✓ Condor expôs a API web
```

---

## 📡 Operação por Componente

### 1. Hummingbot API

**Propósito**: Runtime central para trading, account management, connectors, market data.

**Endpoints Principais**:
```
GET    /health                              # Health check
GET    /accounts/                           # Listar accounts
GET    /connectors/                         # Listar connectors
POST   /accounts/add-account                # Criar account lógica
POST   /accounts/add-credential/...         # Adicionar credenciais
POST   /trading/orders                      # Colocar ordem
POST   /market-data/candles                 # Consultar candles
GET    /portfolio/state                     # Estado do portfolio
```

**Documentação Interativa**:
```
http://localhost:8000/docs  # Swagger UI
```

**Credenciais**:
- Username/Password: definido em `.env.hummingbot`
- MQTT: definido em `.env.mqtt`

**Exemplo de Client**:
```python
import requests

session = requests.Session()
session.auth = ("admin", "admin")

# Health
resp = session.get("http://localhost:8000/health")
print(resp.json())

# Listar accounts
resp = session.get("http://localhost:8000/accounts/")
print(resp.json())

# Candles
resp = session.post(
    "http://localhost:8000/market-data/candles",
    json={
        "connector_name": "binance",
        "trading_pair": "BTC-USDT",
        "interval": "1h",
        "max_records": 100
    }
)
print(resp.json())
```

---

### 2. Quants-Lab

**Propósito**: Motor de estratégia, backtesting, otimização, relatórios e aprovação.

**Operação Manual (Humano)**:
```bash
cd /opt/quants-lab

# Instalação
make install

# Rodar tarefas (coleta de dados, etc)
make run-tasks config=config/tf_pipeline.yml

# Acessar CLI
python cli.py --help
```

**Operação via API (Trinity / Agents)**:
```bash
# Iniciar servidor headless (sem tarefas em background)
python cli.py serve --api-only --port 8075

# Documentação interativa
http://localhost:8075/docs
```

**Endpoints Principais**:
```
POST   /api/v1/strategies/validate          # Validar manifesto de estratégia
POST   /api/v1/backtests                    # Rodar backtest único
POST   /api/v1/optimizations                # Rodar otimização (Optuna)
GET    /api/v1/jobs/{job_id}                # Poll status de job
GET    /api/v1/studies/{study_name}         # Consultar estudo
GET    /api/v1/reports/{study_name}         # Buscar relatório
GET    /api/v1/approvals                    # Listar approval requests
POST   /api/v1/approvals/{approval_id}      # Aprovar/rejeitar
```

**Exemplo: Validar Estratégia**:
```bash
curl -X POST "http://localhost:8075/api/v1/strategies/validate" \
  -H "Content-Type: application/json" \
  --data @examples/trinity/strategy_manifest_macd_bb.json
```

**Exemplo: Rodar Otimização**:
```bash
curl -X POST "http://localhost:8075/api/v1/optimizations" \
  -H "Content-Type: application/json" \
  --data @examples/trinity/optimization_request_macd_bb.json

# Retorna job_id
# {"job_id": "abc123", "status": "PENDING"}

# Poll status
curl "http://localhost:8075/api/v1/jobs/abc123"
```

---

### 3. Condor

**Propósito**: Interface Telegram para monitoramento, trading manual, configuração.

**Operação**:
```bash
cd /opt/condor

# Instalação
make install

# Rodar localmente
make run

# Ou via Docker
make deploy
```

**Documentação API**:
```
http://localhost:8088/docs       # Swagger UI
http://localhost:8088/openapi.json # OpenAPI schema
```

**Autenticação**:
- Token via Telegram: `/web` → gera token → `POST /api/v1/auth/token-login`
- Automação: usar `CONDOR_TOKEN` (Bearer token)

**Exemplo: Listar Servers**:
```bash
curl -sS http://localhost:8088/api/v1/servers \
  -H "Authorization: Bearer ${CONDOR_TOKEN}"
```

**Comandos Telegram**:
```
/portfolio    → Dashboard com PnL
/bots         → Status de bots ativos
/trade        → Menu de trading CEX
/swap         → Trading DEX
/lp           → Gerenciar LP positions
/routines     → Rodar scripts Python
/config       → Configurar servers e credenciais
```

---

### 4. MCP Hummingbot

**Propósito**: Expor tools do Hummingbot para Claude, Gemini e outros AI clients via Model Context Protocol.

**Operação**:
```bash
cd /opt/mcp-hummingbot

# Instalação (modo desenvolvimento)
uv sync

# Ou Docker (produção)
docker pull hummingbot/hummingbot-mcp:latest

# Configurar em Claude Code ou Gemini
# Adicionar config de MCP server (ver .env.example)
```

**Principais MCP Tools**:
```
configure_api_servers()          # Configurar conexão com Hummingbot API
get_portfolio_overview()         # Visão unificada de portfolio
place_order()                    # Colocar ordem
setup_connector()                # Adicionar credenciais
search_history()                 # Buscar histórico de ordens
```

---

### 5. OpenClaw Trinity (Externo)

**Propósito**: Agente de orquestração, geração de estratégias e feedback loop.

**Fluxo Esperado**:
```
1. Trinity coleta contexto (mercado, preferências do usuário)
2. Trinity constrói StrategyManifest
3. Trinity submete para Quants-Lab via API
4. Quants-Lab executa validate/backtest/optimization
5. Trinity lê resultados e relatórios
6. Trinity toma decisão sobre deploy
7. Trinity inicia execução via Hummingbot API
8. Trinity monitora runtime e feedback
9. Loop: aprendizado → nova iteração
```

**Configuração**:
```env
# .env.trinity

# REST APIs
HUMMINGBOT_API_URL=http://localhost:8000
HUMMINGBOT_USERNAME=admin
HUMMINGBOT_PASSWORD=admin

QUANTS_LAB_API_URL=http://localhost:8075
QUANTS_LAB_API_KEY=  # Opcional

CONDOR_API_URL=http://localhost:8088
CONDOR_TOKEN=...

# MQTT
MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USERNAME=trinity_user
MQTT_PASSWORD=...

# MCP (opcional)
MCP_HTTP_URL=http://localhost:3000/mcp
```

---

## 🔌 Contratos de API Unificados

### Quants-Lab: Strategy Manifest

**Estrutura**:
```json
{
  "study_name": "my_study_20240427",
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
```

### Quants-Lab: Backtest Request

```json
{
  "manifest": { ... },
  "candidate_params": {
    "macd_fast_period": 12,
    "macd_slow_period": 26,
    "bb_period": 20
  },
  "export_artifacts": true
}
```

### Quants-Lab: Optimization Request

```json
{
  "manifest": { ... },
  "optimization_config": {
    "direction": "maximize",
    "metric": "sharpe_ratio",
    "n_trials": 100,
    "timeout_minutes": 60
  }
}
```

### Job Status Response

```json
{
  "job_id": "opt-20240427-abc123",
  "study_name": "my_study_20240427",
  "status": "COMPLETED",
  "created_at": "2024-04-27T10:15:00Z",
  "started_at": "2024-04-27T10:15:30Z",
  "completed_at": "2024-04-27T11:45:00Z",
  "result": {
    "best_params": { ... },
    "best_value": 2.15,
    "n_trials_completed": 87,
    "n_trials_total": 100
  }
}
```

### Report Response

```json
{
  "study_name": "my_study_20240427",
  "controller": "macd_bb_v1",
  "date_range": {
    "start": "2024-01-01",
    "end": "2024-03-31"
  },
  "performance": {
    "total_return": "15.3%",
    "sharpe_ratio": 2.15,
    "max_drawdown": "-4.2%",
    "win_rate": "62.5%",
    "num_trades": 47
  },
  "approval_request": {
    "status": "PENDING",
    "created_at": "2024-04-27T11:45:00Z",
    "expires_at": "2024-04-29T11:45:00Z"
  },
  "artifacts": {
    "report_url": "/api/v1/reports/my_study_20240427/report.html",
    "equity_curve_url": "/api/v1/reports/my_study_20240427/equity_curve.png",
    "human_report_url": "/api/v1/reports/my_study_20240427/human_report.md"
  }
}
```

### Approval Decision

```json
{
  "approval_id": "appr-20240427-xyz789",
  "action": "APPROVE",  // or "REJECT"
  "decision_maker": "trader_admin",
  "notes": "Approved for live trading. Start with 10% position size."
}
```

---

## 🔄 Fluxos de Integração

### Fluxo 1: Trinity → Backtest → Deploy

```
[Trinity Agent]
     ↓ (POST /api/v1/strategies/validate)
[Quants-Lab] ← Valida manifesto
     ↓ (POST /api/v1/backtests)
[Quants-Lab] ← Executa backtest único
     ↓ (GET /api/v1/jobs/{job_id}) [polling]
[Job concluído]
     ↓ (GET /api/v1/reports/{study_name})
[Trinity lê relatório]
     ↓ (POST /api/v1/executors) [via Hummingbot API]
[Hummingbot] ← Deploy do executor com params
     ↓ (GET /portfolio/state)
[Trinity monitora]
```

**Tempo estimado**: 5-30 min (depende do período de backtest)

### Fluxo 2: Trinity → Otimização → Aprovação → Deploy

```
[Trinity Agent]
     ↓ (POST /api/v1/strategies/validate)
[Quants-Lab] ← Valida manifesto
     ↓ (POST /api/v1/optimizations)
[Quants-Lab] ← Lança job de otimização
     ↓ (GET /api/v1/jobs/{job_id}) [polling]
[Job em progresso...]
     ↓ (Status: 87/100 trials)
[Horas após conclusão...]
     ↓ (GET /api/v1/reports/{study_name})
[Trinity lê relatório + approval request]
     ↓ (approval.status == "PENDING")
[Trinity/Admin aprova] (POST /api/v1/approvals/{approval_id})
     ↓ (approval.status == "APPROVED")
[Hummingbot] ← Deploy (via Trinity ou Condor)
```

**Tempo estimado**: 1-24 horas (depende da complexidade)

### Fluxo 3: Condor → Portfolio Check → Manual Trade

```
[Operador] → /portfolio
     ↓ (GET /api/v1/portfolio/state)
[Condor]
     ↓ (Telegram: exibe PnL, holdings)
[Operador] → /trade
     ↓ (Seleciona CEX, par, lado, amount)
[Condor] → (POST /trading/orders via Hummingbot)
     ↓ (Retorna order_id)
[Operador] → confirma
     ↓ (Order executada)
[Condor] → (Monitora via GET /trading/orders)
```

**Tempo estimado**: < 1 min (por trade)

### Fluxo 4: AI Assistant (Claude) → MCP Tools

```
[Claude via MCP]
     ↓ (configure_api_servers())
[MCP Hummingbot]
     ↓ (Configura conexão)
[Claude] → (get_portfolio_overview())
     ↓ (MCP consulta /portfolio/state)
[Hummingbot API]
     ↓ (Retorna portfolio unificado)
[Claude] → "Seu portfolio contém..."
```

---

## 📋 Procedimentos Operacionais

### Procedimento 1: Setup Inicial da Suite

**Responsável**: DevOps / Platform Engineer

```bash
# 1. Clonar e configurar
cd /opt
./hummingbot-api/scripts/validate_prerequisites.sh

# 2. Bootstrap automático
./hummingbot-api/scripts/bootstrap_stack.sh

# 3. Validar
./hummingbot-api/scripts/validate_integrations.sh

# 4. Testar fluxo básico (via curl)
curl -u admin:admin http://localhost:8000/health
curl http://localhost:8075/docs
curl http://localhost:8088/docs

# 5. Documentar estado
echo "✅ Suite pronta para operação"
```

### Procedimento 2: Deploy de Estratégia (Trinity)

**Responsável**: Agent/Trinity ou Operador

```bash
# 1. Validar manifesto
curl -X POST "http://localhost:8075/api/v1/strategies/validate" \
  -H "Content-Type: application/json" \
  -d @strategy_manifest.json

# Esperado: {"valid": true, ...}

# 2. Rodar otimização
JOB_ID=$(curl -X POST "http://localhost:8075/api/v1/optimizations" \
  -H "Content-Type: application/json" \
  -d @optimization_request.json | jq -r '.job_id')

# 3. Aguardar conclusão (polling)
while true; do
  STATUS=$(curl "http://localhost:8075/api/v1/jobs/${JOB_ID}" | jq -r '.status')
  if [[ "$STATUS" == "COMPLETED" ]]; then break; fi
  echo "Status: $STATUS"
  sleep 30
done

# 4. Ler relatório
curl "http://localhost:8075/api/v1/reports/$(curl http://localhost:8075/api/v1/jobs/${JOB_ID} | jq -r '.study_name')"

# 5. Se approval.status == "PENDING", aprovar
APPROVAL_ID=$(curl "http://localhost:8075/api/v1/approvals" | jq -r '.[0].approval_id')
curl -X POST "http://localhost:8075/api/v1/approvals/${APPROVAL_ID}" \
  -H "Content-Type: application/json" \
  -d '{"action": "APPROVE", "notes": "..."}'

# 6. Deploy via Hummingbot API
curl -u admin:admin -X POST "http://localhost:8000/executors/" \
  -H "Content-Type: application/json" \
  -d '{
    "account_name": "master_account",
    "controller_type": "macd_bb",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
    "parameters": {...}
  }'
```

### Procedimento 3: Monitoramento via Condor

**Responsável**: Operador de Trading

```bash
# 1. Acessar Telegram
# Clique em /portfolio

# 2. Revisar PnL e holdings
# Telegram exibe:
# - 24h/7d/30d returns
# - Holdings com preços
# - Positions abertas
# - Orders ativas

# 3. Se necessário, ajustar
# Clique em /trade
# Selecione: CEX → Connector → Pair → BUY/SELL → Amount → Price

# 4. Confirmar e executar
# Order é colocada e rastreada em tempo real
```

### Procedimento 4: Troubleshooting de Componente

**Responsável**: Engineer / DevOps

```bash
# 1. Health Check Geral
./hummingbot-api/scripts/healthcheck.sh

# 2. Verificar logs de componente específico
docker compose logs -f hummingbot-api
docker compose logs -f quants-lab  # Se em Docker
docker compose logs -f condor
docker compose logs -f mcp-hummingbot

# 3. Testar conectividade
curl -u admin:admin http://localhost:8000/health
curl http://localhost:8075/health
curl http://localhost:8088/health
curl http://localhost:3000/health

# 4. Validar integração
./hummingbot-api/scripts/validate_integrations.sh

# 5. Se API falha, verificar:
# - PostgreSQL: docker exec hummingbot-postgres psql -U $USER -d $DB -c "SELECT COUNT(*) FROM account_states;"
# - EMQX: http://localhost:18083 (admin/public)
# - MCP: curl http://localhost:3000/mcp

# 6. Resetar dados se necessário
docker compose down -v  # ⚠️ Remove volume!
./scripts/bootstrap_stack.sh
```

---

## 🎯 Checklist de Operação Diária

- [ ] Healthcheck geral: `./scripts/healthcheck.sh`
- [ ] Revisar alertas de EMQX (http://localhost:18083)
- [ ] Revisar approvals pendentes: GET `/api/v1/approvals`
- [ ] Monitora bots ativos via Condor `/bots`
- [ ] Confirmar backups de MongoDB (se em produção)
- [ ] Revisar logs de trading (sucesso/falhas)
- [ ] Aprovar/rejeitar strategy candidates
- [ ] Feedback loop: Trinity atualiza learnings

---

## ⚠️ Troubleshooting

### Problema: API retorna 401 Unauthorized

**Causa**: Credenciais erradas ou session expirada

**Solução**:
```bash
# Verificar credenciais em .env
cat .env.hummingbot | grep USERNAME

# Testar com curl
curl -u admin:admin http://localhost:8000/health

# Regenerar credentials se necessário
docker compose exec hummingbot-api python -m hummingbot.user --reset-credentials
```

### Problema: Job de otimização está "travado" em PENDING

**Causa**: Quants-Lab não consegue acessar dados ou executor lento

**Solução**:
```bash
# Verificar logs de Quants-Lab
docker compose logs quants-lab | grep job_id

# Verificar se MongoDB está rodando
docker exec quants-lab-mongo mongosh --eval "db.version()"

# Reiniciar job
curl -X POST "http://localhost:8075/api/v1/jobs/{job_id}/cancel"
```

### Problema: Condor não consegue se conectar a Hummingbot API

**Causa**: Network ou credenciais

**Solução**:
```bash
# Testar conectividade
curl -u admin:admin http://localhost:8000/health

# Verificar .env.condor
cat .env.condor | grep HUMMINGBOT_API

# Se em Docker, usar host.docker.internal (Mac/Windows) ou redes Docker
```

### Problema: Trinity não consegue acessar Quants-Lab

**Causa**: Quants-Lab não está rodando ou porta errada

**Solução**:
```bash
# Verificar se Quants-Lab está rodando
ps aux | grep "python cli.py serve"

# Iniciar manualmente
cd /opt/quants-lab
python cli.py serve --api-only --port 8075

# Verificar conectividade
curl http://localhost:8075/health
```

---

## 📊 Monitoramento e Observabilidade

### Logs Centralizados

```bash
# Todos os logs
docker compose logs -f

# Específico por serviço
docker compose logs -f hummingbot-api
docker compose logs -f mcp-hummingbot
docker compose logs -f emqx
```

### Métricas via EMQX Dashboard

```
http://localhost:18083
Credenciais: admin / public

Monitore:
- Conexões MQTT ativas
- Mensagens publicadas/subscritas
- Saúde do broker
```

### Dados de Performance

**Backtests e Optimizations**:
```bash
# Listar estudos
curl http://localhost:8075/api/v1/studies

# Detalhe de estudo
curl http://localhost:8075/api/v1/studies/my_study_name

# Artigos do relatório
curl http://localhost:8075/api/v1/reports/my_study_name/equity_curve.csv
```

**Portfolio e Trading**:
```bash
# Estado atual
curl -u admin:admin http://localhost:8000/portfolio/state

# Histórico de ordens
curl -u admin:admin http://localhost:8000/trading/orders/search
```

---

## 🔐 Segurança e Credenciais

### Hierarquia de Acesso

```
┌─────────────────────────────────────┐
│ Hummingbot API                      │
│ - Credencial: USERNAME/PASSWORD     │
│ - MCP: usa mesma credencial         │
└─────────────────────────────────────┘
         ↓
┌─────────────────────────────────────┐
│ Quants-Lab                          │
│ - Sem auth (executado localmente)   │
│ - Opcional: API key se exposto      │
└─────────────────────────────────────┘
         ↓
┌─────────────────────────────────────┐
│ Condor                              │
│ - Token: CONDOR_TOKEN (Bearer)      │
│ - Telegram: whitelist de ADMIN_ID   │
└─────────────────────────────────────┘
         ↓
┌─────────────────────────────────────┐
│ MQTT (EMQX)                         │
│ - Username/Password: MQTT_USER/PASS │
│ - Tópicos segregados por role       │
└─────────────────────────────────────┘
```

### Best Practices

1. **Nunca commitir credenciais** → usar `.env` e `.gitignore`
2. **Rotacionar senhas regularmente**
3. **HTTPS em produção** → configurar SSL/TLS em nginx/reverse proxy
4. **Limpar secrets em logs** → scripts sanitizam senhas antes de logar
5. **Whitelist de IPs** → se expostos publicamente

---

## 🧩 Próximas Camadas

### 1. Axodus Web UI (Q2 2026)

Interface comercial para:
- Builder de estratégias (visual + JSON)
- Backtests e otimizações
- Approval queue
- Reports e equity curves
- Portfolio dashboard
- Documentação de estratégias

**Consumirá**: Quants-Lab API (porta 8075) + Hummingbot API (porta 8000)

### 2. Trinity Client Completo

Implementação robusta do agente com:
- Pool de manifestos candidatos
- Heurísticas de seleção
- Loop de feedback automático
- Aprendizado com histórico
- Escalação para humanos

### 3. Self-Learning Loop

Retroalimentação automática:
- Trinity lê resultados pós-deploy
- Ajusta ranges de parâmetros
- Atualiza filtros de validação
- Melhora continuamente

### 4. Multi-Tenant / SaaS Layer

Escalabilidade para múltiplos usuários:
- Isolamento de dados por tenant
- Quotas de recursos
- Billing + monitoring
- Auditoria e compliance

---

## 📚 Referências e Documentação

**Repositórios**:
- [hummingbot-api](https://github.com/Axodus/hummingbot-api) - Runtime
- [quants-lab](https://github.com/Axodus/quants-lab) - Backtesting
- [condor](https://github.com/Axodus/condor) - Monitoring
- [mcp-hummingbot](https://github.com/Axodus/mcp-hummingbot) - AI Integration
- [Tradingbot](https://github.com/Axodus/Tradingbot) - Estratégias

**Documentação Oficial**:
- [Hummingbot API Reference](/opt/hummingbot-api/API_REFERENCE.md)
- [Hummingbot Setup Guide](/opt/hummingbot-api/docs/SETUP.md)
- [Hummingbot Trinity Integration](/opt/hummingbot-api/docs/TRINITY_INTEGRATION.md)
- [Quants-Lab Trinity Integration](/opt/quants-lab/docs/trinity-integration.md)
- [Quants-Lab Suite Status](/opt/quants-lab/docs/axodus-trading-suite-status.md)

**Endpoints ao Vivo**:
- Hummingbot Swagger: http://localhost:8000/docs
- Quants-Lab Swagger: http://localhost:8075/docs
- Condor Swagger: http://localhost:8088/docs
- EMQX Dashboard: http://localhost:18083

---

## 📞 Suporte e Escalação

### Canais

| Canal | Propósito | Exemplo |
|-------|-----------|---------|
| **Issues (GitHub)** | Bugs, features | API 500 error |
| **Discussions (GitHub)** | Dúvidas, design | Como integrar novo connector? |
| **Telegram (Condor)** | Operação, trading | Portfolio, orders |
| **Logs** | Debugging | `docker compose logs -f` |

### Escalação

1. **Operador**: Checkear healthcheck + logs
2. **Engineer**: Testar integrações + APIs
3. **DevOps**: Infraestrutura + containers
4. **Product**: Requisitos + roadmap

---

## 🗒️ Histórico de Versões

| Versão | Data | Mudanças |
|--------|------|----------|
| 1.0 | 27/04/2026 | Inicial - consolidação de integração |

---

**Última Atualização**: 27/04/2026  
**Mantidor**: Axodus Engineering  
**Status**: ✅ Pronto para Operação
