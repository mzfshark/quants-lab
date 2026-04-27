# Axodus Trading Suite - Runbooks por Persona

**Data**: Abril 2026  
**Versão**: 1.0  
**Propósito**: Procedures passo a passo para cada role/persona operacional.

---

## Nota de Ambiente (Windows)

Alguns exemplos usam bash (`.sh`, `export`, `jq`, `grep`, `sqlite3`). Em Windows/PowerShell:

- Variáveis de ambiente: use `$env:NOME="valor"`
- `curl`: prefira `curl.exe` (não o alias do PowerShell)
- Para scripts `.sh`: rode via WSL/Git-Bash, ou substitua por checks HTTP equivalentes

## 👥 Personas Operacionais

```
┌─────────────────────────────────────┐
│ Trinity Agent (Externo)             │ ← AI que cria e deploya estratégias
├─────────────────────────────────────┤
│ Operador de Trading                 │ ← Monitora, aprova, executa trades
├─────────────────────────────────────┤
│ Engineer / DevOps                   │ ← Setup, infra, troubleshooting
├─────────────────────────────────────┤
│ Analista de Quant                   │ ← Design, backtesting, otimização
├─────────────────────────────────────┤
│ Admin / Governance                  │ ← Políticas, aprovações, auditoria
└─────────────────────────────────────┘
```

---

## 1️⃣ Trinity Agent - Runbook

### Persona: Agente de Inteligência Artificial
- **Responsabilidade**: Criar, testar, deploy e monitorar estratégias
- **Ferramentas**: API REST, MQTT, MCP (opcional)
- **Frequência**: Contínua (24/7)
- **Scope**: Quants-Lab + Hummingbot API

---

### Pré-requisitos

```bash
# Verificar conectividade
curl -s http://localhost:8075/health | jq .
curl -u admin:admin http://localhost:8000/health | jq .

# Ter credenciais configuradas em .env.trinity
export QUANTS_LAB_URL="http://localhost:8075"
export HUMMINGBOT_API_URL="http://localhost:8000"
export HUMMINGBOT_USERNAME="admin"
export HUMMINGBOT_PASSWORD="admin"
```

```powershell
# (Windows / PowerShell) Verificar conectividade
curl.exe -sS http://127.0.0.1:8075/health
curl.exe -sS http://127.0.0.1:8000/health

# Variáveis de ambiente
$env:QUANTS_LAB_URL="http://127.0.0.1:8075"
$env:HUMMINGBOT_API_URL="http://127.0.0.1:8000"
$env:HUMMINGBOT_USERNAME="admin"
$env:HUMMINGBOT_PASSWORD="admin"
```

---

### Fluxo 1: Criação e Deploy de Estratégia (Rápido)

**Tempo**: 5-30 minutos  
**Entrada**: Contexto de mercado, objetivo, restrições  
**Saída**: Estratégia deployada ou rejeitada

#### Passo 1: Coleta de Contexto

```python
# Coletar estado de mercado
import requests
import json

session = requests.Session()
session.auth = ("admin", "admin")

# 1.1 Estado do portfolio atual
portfolio = session.get(
    f"{HUMMINGBOT_API_URL}/portfolio/state"
).json()

current_balance = portfolio['accounts'][0]['balances']['USDT']['total']
print(f"Saldo USDT: ${current_balance}")

# 1.2 Candles históricos (últimos 90 dias)
candles = session.post(
    f"{HUMMINGBOT_API_URL}/market-data/candles",
    json={
        "connector_name": "binance",
        "trading_pair": "BTC-USDT",
        "interval": "1d",
        "max_records": 90
    }
).json()

# 1.3 Análise (correlação, volatilidade, etc.)
btc_prices = [c['close'] for c in candles['candles']]
volatility = std(btc_prices) / mean(btc_prices)
print(f"Volatilidade BTC: {volatility:.2%}")
```

#### Passo 2: Construir Manifesto

```python
# 2.1 Definir estratégia
manifest = {
    "study_name": f"trinity_btc_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    "controller": "macd_bb_v1",
    "controller_type": "directional",
    "connector": "binance",
    "trading_pair": "BTC-USDT",
    "candle_size": "1h",
    "start_date": "2024-01-01",
    "end_date": "2024-03-31",
    "initial_portfolio": {
        "BTC": 0.0,
        "USDT": current_balance * 0.8  # 80% da carteira
    },
    "parameter_ranges": {
        "macd_fast_period": {"min": 6, "max": 14},
        "macd_slow_period": {"min": 20, "max": 32},
        "bb_period": {"min": 15, "max": 25}
    },
    "fixed_parameters": {
        "grid_levels": 5,
        "take_profit_ratio": 0.02
    },
    "filters": {
        "max_loss_pct": -5.0,
        "min_sharpe_ratio": 1.0
    }
}

# 2.2 Validar manifesto
response = requests.post(
    f"{QUANTS_LAB_URL}/api/v1/strategies/validate",
    json=manifest
)

if not response.json()['valid']:
    print(f"Manifesto inválido: {response.json()['errors']}")
    return  # Volta ao passo 1
else:
    print("✓ Manifesto válido")
```

#### Passo 3: Rodar Otimização

```python
# 3.1 Submeter job de otimização
job_response = requests.post(
    f"{QUANTS_LAB_URL}/api/v1/optimizations",
    json={
        "manifest": manifest,
        "optimization_config": {
            "direction": "maximize",
            "metric": "sharpe_ratio",
            "n_trials": 100,
            "timeout_minutes": 120
        }
    }
).json()

job_id = job_response['job_id']
print(f"Job iniciado: {job_id}")

# 3.2 Poll status (a cada 30 segundos)
import time
while True:
    status = requests.get(
        f"{QUANTS_LAB_URL}/api/v1/jobs/{job_id}"
    ).json()
    
    if status['status'] == 'COMPLETED':
        print(f"✓ Otimização completa: {status['result']['best_value']:.2f}")
        break
    elif status['status'] == 'FAILED':
        print(f"✗ Job falhou: {status['error']['message']}")
        return
    else:
        progress = status['progress']
        print(f"  {progress['percentage']:.0f}% ({progress['current_trial']}/{progress['total_trials']} trials)")
        time.sleep(30)
```

#### Passo 4: Ler Relatório e Aprovação

```python
# 4.1 Buscar relatório
study_name = manifest['study_name']
report = requests.get(
    f"{QUANTS_LAB_URL}/api/v1/reports/{study_name}"
).json()

print(f"""
Performance Summary:
  Total Return: {report['summary']['total_return']}
  Sharpe Ratio: {report['summary']['sharpe_ratio']}
  Max Drawdown: {report['summary']['max_drawdown']}
  Win Rate: {report['summary']['win_rate']}
  Trades: {report['summary']['num_trades']}
""")

# 4.2 Verificar approval request
approval_request = report['approval_request']
print(f"Approval Status: {approval_request['status']}")

if approval_request['status'] != 'PENDING':
    print("Estratégia bloqueada pela validação")
    return
```

#### Passo 5: Deploy (Se Aprovado)

```python
# 5.1 (Opcional) Aprovar se política permite
approval_id = approval_request['approval_id']
requests.post(
    f"{QUANTS_LAB_URL}/api/v1/approvals/{approval_id}",
    json={
        "action": "APPROVE",
        "decision_maker": "trinity_agent",
        "notes": "Approved by Trinity automatic policy"
    }
)

# 5.2 Deploy executor no Hummingbot
deploy_response = session.post(
    f"{HUMMINGBOT_API_URL}/executors/",
    json={
        "account_name": "master_account",
        "controller_type": "macd_bb_v1",
        "connector_name": "binance",
        "trading_pair": "BTC-USDT",
        "parameters": report['approval_request'].get('best_params', {}),
        "execution_mode": "paper"  # "paper" ou "live"
    }
).json()

print(f"✓ Executor deployado: {deploy_response['executor_id']}")
```

#### Passo 6: Monitoramento

```python
# 6.1 Poll executores ativos
while True:
    executores = session.get(
        f"{HUMMINGBOT_API_URL}/executors/"
    ).json()
    
    for executor in executores:
        print(f"""
Executor: {executor['id']}
  Status: {executor['status']}
  PnL: ${executor['performance']['pnl']}
  Trades: {executor['performance']['trade_count']}
  Last Activity: {executor['last_activity']}
""")
    
    time.sleep(60)
```

---

### Fluxo 2: Aprendizado e Ajuste (Semanal)

**Tempo**: 30 minutos  
**Entrada**: Histórico de deployments  
**Saída**: Novos ranges, filtros ajustados

```python
# 2.1 Coletar resultados pós-deploy
studies = requests.get(
    f"{QUANTS_LAB_URL}/api/v1/studies"
).json()

# Filtrar últimas semana
recent = [s for s in studies['items'] 
          if (datetime.now() - parse(s['created_at'])).days <= 7]

# 2.2 Análise de performance
total_return = sum([s['backtesting_results']['performance']['total_return'] 
                    for s in recent])
success_rate = len([s for s in recent 
                    if s['approval_request']['status'] == 'APPROVED']) / len(recent)

print(f"Última semana: +{total_return:.2%} com {success_rate:.0%} aprovação")

# 2.3 Ajustar heurísticas
if success_rate < 0.5:
    print("→ Taxa de aprovação baixa, relaxar filtros")
    # Reduzir min_sharpe_ratio, aumentar max_loss_pct
    
if total_return < 0:
    print("→ Performance negativa, ajustar parameter ranges")
    # Aumentar diversificação de parâmetros
```

---

## 2️⃣ Operador de Trading - Runbook

### Persona: Humano responsável por decisões de trading
- **Responsabilidade**: Monitorar, aprovar, executar trades manualmente se necessário
- **Ferramentas**: Condor (Telegram), Quants-Lab API, Hummingbot API
- **Frequência**: Diária (8h/dia) ou 24/7 (shifts)
- **Scope**: Approval queue, portfolio, execução manual

---

### Pré-requisitos

```bash
# Configurar bot Telegram
# 1. Crie bot em @BotFather
# 2. Configure TELEGRAM_TOKEN em .env.condor
# 3. Agora use /portfolio, /bots, /trade, etc.

# Ou use API direto
export CONDOR_TOKEN="..."
curl http://localhost:8088/api/v1/portfolio \
  -H "Authorization: Bearer ${CONDOR_TOKEN}"
```

---

### Checklist de Início de Dia

```
□ 08:00 → Abrir Telegram, clicar /portfolio
          Ver saldo, PnL 24h, holdings principais
          
□ 08:05 → /bots
          Verificar se bots estão RUNNING
          Notar qualquer executor em STOPPED ou ERROR
          
□ 08:10 → Acessar http://localhost:8075/api/v1/approvals
          Listar approval requests PENDING
          Ler cada uma e decidir APPROVE ou REJECT
          
□ 08:20 → Revisar ordens abertas
          Clicar /trade → Ver OPEN orders
          Cancelar se mercado mudou significativamente
          
□ 08:30 → Monitoramento contínuo
          Observar alerts (se configurados)
          Estar pronto para intervir se volatilidade extrema
```

---

### Fluxo: Aprovar Estratégia

**Quando**: Quando Trinity submete study para aprovação  
**Tempo**: 10-30 minutos  
**Ferramenta**: Browser + Condor

#### Passo 1: Receber Notificação

```
Telegram (Condor bot):
"📊 Nova estratégia para aprovação: trinity_btc_20240427_101530
  Sharpe: 2.15 | Retorno: 15.3% | MaxDD: -4.2%
  Clique /approvals para revisar"
```

#### Passo 2: Revisar Relatório

```bash
# Via curl
curl http://localhost:8075/api/v1/reports/trinity_btc_20240427_101530 | jq

# Output esperado:
{
  "summary": {
    "total_return": "15.3%",
    "sharpe_ratio": 2.15,
    "max_drawdown": "-4.2%",
    "win_rate": "62.5%",
    "num_trades": 47
  },
  "approval_request": {
    "approval_id": "appr-20240427-abc123",
    "status": "PENDING",
    "context": {
      "risks": [
        "High leverage (5x)",
        "Single pair strategy"
      ],
      "recommendations": [
        "Start with 10% position size",
        "Monitor for correlation with other pairs"
      ]
    }
  }
}
```

#### Passo 3: Decider APPROVE ou REJECT

**Critério de Aprovação**:
```
✓ APPROVE se:
  - Sharpe Ratio > 1.5
  - Win Rate > 50%
  - Max Drawdown < -5%
  - Recomendações claras documentadas
  
✗ REJECT se:
  - Algo não passa nas validações automáticas
  - Backtest é muito "curve fit" (perfect performance)
  - Risco é inaceitável para posição size
  - Pares correlacionados
```

#### Passo 4: Executar Decisão

```bash
# APROVAÇÃO
curl -X POST http://localhost:8075/api/v1/approvals/appr-20240427-abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "action": "APPROVE",
    "decision_maker": "operator_john",
    "notes": "Approved for live. Start with 5% allocation. Monitor correlation risk."
  }'

# OU REJEIÇÃO
curl -X POST http://localhost:8075/api/v1/approvals/appr-20240427-abc123 \
  -H "Content-Type: application/json" \
  -d '{
    "action": "REJECT",
    "decision_maker": "operator_john",
    "notes": "Curve fit too high. Drawdown unacceptable. Request lower leverage."
  }'
```

---

### Fluxo: Executar Trade Manual

**Quando**: Oportunidade tática ou se executor está offline  
**Tempo**: 2-5 minutos

#### Via Telegram (Condor)

```
1. Clicar /trade no Telegram
2. Selecionar conta (master_account)
3. Selecionar connector (binance)
4. Selecionar pair (BTC-USDT)
5. Side: BUY
6. Quantidade: 0.05
7. Tipo: LIMIT
8. Preço: 42000
9. Confirmar
```

#### Via API

```bash
curl -u admin:admin -X POST \
  http://localhost:8000/trading/orders \
  -H "Content-Type: application/json" \
  -d '{
    "account_name": "master_account",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
    "order_type": "LIMIT",
    "side": "BUY",
    "amount": 0.05,
    "price": 42000.0
  }'

# Response:
{
  "order_id": "ord-20240427-xyz",
  "status": "OPEN",
  "amount": 0.05,
  "price": 42000.0,
  "created_at": "2024-04-27T10:15:00Z"
}
```

---

### Fluxo: Monitoramento 24/7 (Shifts)

```bash
# Monitorar cada 5 minutos
watch -n 300 'curl -s http://localhost:8088/api/v1/portfolio | jq .'

# Ou em loop
while true; do
  curl -s http://localhost:8088/api/v1/bots | jq '.bots[] | select(.status != "RUNNING")'
  sleep 300
done
```

---

## 3️⃣ Engineer / DevOps - Runbook

### Persona: Responsável por infraestrutura e operação técnica
- **Responsabilidade**: Setup, deploy, troubleshooting, monitoring
- **Ferramentas**: Docker, shell scripts, logs
- **Frequência**: Diária para monitoring, conforme needed para deploy
- **Scope**: Toda a stack

---

### Setup Inicial

```bash
cd /opt/hummingbot-api

# 1. Validar pré-requisitos
./scripts/validate_prerequisites.sh

# 2. Preencher variáveis de ambiente
cp .env.example .env.postgres
cp .env.example .env.mqtt
cp .env.example .env.hummingbot
cp .env.example .env.mcp
cp .env.example .env.condor

# Editar arquivos com valores reais
nano .env.hummingbot
nano .env.condor

# 3. Bootstrap automático (tudo de uma vez)
./scripts/bootstrap_stack.sh

# 4. Validar status
./scripts/validate_integrations.sh
./scripts/healthcheck.sh

# 5. Testar fluxo básico
curl -u admin:admin http://localhost:8000/health
curl http://localhost:8075/health
curl http://localhost:8088/health
```

```powershell
# (Windows / PowerShell)
Set-Location Z:\opt\hummingbot-api

# Scripts em ./scripts são bash (.sh). Se estiver sem WSL/Git-Bash, pule os .sh e valide via HTTP:
Copy-Item .env.example .env.postgres
Copy-Item .env.example .env.mqtt
Copy-Item .env.example .env.hummingbot
Copy-Item .env.example .env.mcp
Copy-Item .env.example .env.condor

# Editar arquivos com valores reais (exemplo: Notepad)
notepad .env.hummingbot
notepad .env.condor

# Validar status via HTTP
curl.exe -sS http://127.0.0.1:8000/health
curl.exe -sS http://127.0.0.1:8075/health
curl.exe -sS http://127.0.0.1:8088/health
```

---

### Monitoring Diário

```bash
# Daily healthcheck
/opt/hummingbot-api/scripts/healthcheck.sh

# Se algum serviço falhar:
docker compose logs -f <service_name>

# Containers em execução
docker compose ps

# Volume usage
docker system df
```

---

### Troubleshooting

#### API retorna 401

```bash
# Verificar credenciais
cat .env.hummingbot | grep USERNAME

# Reset credentials se necessário
docker compose down
docker volume rm hummingbot_api_postgres_data
./scripts/bootstrap_stack.sh
```

#### Job "travado" em PENDING

```bash
# Ver logs de Quants-Lab
docker compose logs quants-lab | tail -100 | grep ERROR

# Reiniciar serviço
docker compose restart quants-lab

# Ou, se em produção, usar:
python cli.py jobs cancel <job_id>
```

#### EMQX connection errors

```bash
# EMQX Dashboard
http://localhost:18083
# Login: admin/public

# Ver connections
docker exec emqx emqx ctl status

# Reset se needed
docker compose restart emqx
docker compose exec -i emqx /opt/emqx/bin/emqx.sh eval rpc:multicall([node()], emqx_retainer, clean, [])
```

#### PostgreSQL data corruption

```bash
# Backup antes de limpar
docker exec hummingbot-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" > backup_$(date +%s).sql

# Reset dados (⚠️ DESTRUTIVO)
docker compose down -v
./scripts/bootstrap_stack.sh
```

---

### Deployment de Nova Versão

```bash
cd /opt/hummingbot-api

# 1. Pull latest
git pull origin main

# 2. Stop containers
docker compose down

# 3. Rebuild images
docker compose build --no-cache

# 4. Start
docker compose up -d

# 5. Validate
./scripts/healthcheck.sh
./scripts/validate_integrations.sh

# 6. Notify ops
echo "✓ Deployment complete. All services running."
```

```powershell
# (Windows / PowerShell)
Set-Location Z:\opt\hummingbot-api

# 1. Pull latest
git pull origin main

# 2. Stop containers
docker compose down

# 3. Rebuild images
docker compose build --no-cache

# 4. Start
docker compose up -d

# 5. Validate (HTTP)
curl.exe -sS http://127.0.0.1:8000/health
curl.exe -sS http://127.0.0.1:8075/health
curl.exe -sS http://127.0.0.1:8088/health
```

---

### Backup and Restore

```bash
# Backup (weekly)
mkdir -p /backups/$(date +%Y%m%d)
docker exec hummingbot-postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > /backups/$(date +%Y%m%d)/postgres.sql.gz
mongodump --out /backups/$(date +%Y%m%d)/mongo --uri "$MONGO_URI"

# Restore
gunzip -c /backups/20240427/postgres.sql.gz | docker exec -i hummingbot-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
mongorestore --uri "$MONGO_URI" /backups/20240427/mongo
```

---

## 4️⃣ Analista de Quant - Runbook

### Persona: Designs and validates trading strategies
- **Responsabilidade**: Validar controllers, ajustar parameter ranges, análise estatística
- **Ferramentas**: Quants-Lab CLI, Jupyter, scripts Python
- **Frequência**: 2-3x por semana
- **Scope**: Controllers, backtesting, otimização

---

### Fluxo: Design e Teste de Novo Controller

```bash
cd /opt/quants-lab

# 1. Criar novo controller
mkdir app/controllers/my_new_controller_v1
# Implementar lógica em Python

# 2. Validar syntaxe
python -m py_compile app/controllers/my_new_controller_v1/__init__.py

# 3. Preparar manifesto de teste
cat > examples/testing/manifest_my_new.json << 'EOF'
{
  "study_name": "test_my_new_controller",
  "controller": "my_new_controller_v1",
  "controller_type": "directional",
  "connector": "binance",
  "trading_pair": "BTC-USDT",
  "candle_size": "1h",
  "start_date": "2024-01-01",
  "end_date": "2024-03-31",
  "initial_portfolio": {"BTC": 1.0, "USDT": 10000.0},
  "parameter_ranges": {
    "param_1": {"min": 10, "max": 50},
    "param_2": {"min": 0.01, "max": 0.1}
  },
  "filters": {"min_sharpe_ratio": 1.0}
}
EOF

# 4. Rodar Quants-Lab em modo desenvolvimento
python cli.py serve --api-only --port 8075

# 5. Validar (em outro terminal)
curl -X POST http://localhost:8075/api/v1/strategies/validate \
  -H "Content-Type: application/json" \
  --data @examples/testing/manifest_my_new.json

# 6. Rodar backtest único
curl -X POST http://localhost:8075/api/v1/backtests \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {...},
    "candidate_params": {"param_1": 30, "param_2": 0.05},
    "export_artifacts": true
  }'

# 7. Revisar resultados
# Abrir HTML report: app/outputs/test_my_new_controller/report.html

# 8. Se OK, fazer pull request
git checkout -b feat/my_new_controller
git add app/controllers/my_new_controller_v1/
git commit -m "Add my_new_controller_v1"
git push origin feat/my_new_controller
```

---

### Fluxo: Análise de Resultados

```python
# Jupyter notebook

import pandas as pd
import numpy as np
from app.controllers.analysis import analyze_study

# Carregar estudo
study = analyze_study("trinity_btc_20240427_101530")

# Equity curve
study.plot_equity_curve()

# Drawdown analysis
study.plot_drawdown()

# Trade distribution
study.plot_trades()

# Correlation analysis
study.correlation_matrix()

# Recomendações
print(study.recommendations())
```

---

## 5️⃣ Admin / Governance - Runbook

### Persona: Política, compliance, decisões de escalação
- **Responsabilidade**: Definir políticas, aprovar mudanças, auditoria
- **Ferramentas**: API, logs, databases
- **Frequência**: Semanal ou conforme demanda
- **Scope**: Approvals críticas, escalações

---

### Fluxo: Revision Policy

**Quando**: Mudanças de risco aceito, limites de posição, etc.

```bash
# 1. Revisar aprovações dos últimos 7 dias
curl http://localhost:8075/api/v1/approvals \
  | jq '.approvals | length'

# 2. Ver taxa de rejeição
TOTAL=$(curl http://localhost:8075/api/v1/approvals | jq '.approvals | length')
REJECTED=$(curl http://localhost:8075/api/v1/approvals | jq '[.approvals[] | select(.status == "REJECTED")] | length')
echo "Taxa rejeição: $((REJECTED * 100 / TOTAL))%"

# 3. Se muito alta, rever filtros
# Se muito baixa, apertar filtros

# 4. Documentar mudança
cat > GOVERNANCE_CHANGE_LOG.md << 'EOF'
## 2024-04-27

### Mudança: Aumentar min_sharpe_ratio
- De: 1.0
- Para: 1.5
- Razão: Taxa aprovação estava 90%, muito alta
- Aprovado por: admin_alice
- Data: 2024-04-27

EOF
```

---

### Fluxo: Auditoria Mensal

```bash
# 1. Contar estratégias deployadas
curl http://localhost:8075/api/v1/studies | jq '[.items[] | select(.approval_request.status == "APPROVED")] | length'

# 2. PnL acumulado
curl http://localhost:8075/api/v1/studies | jq '[.items[] | .backtesting_results.performance.total_return] | add'

# 3. Relatório de aprovações
sqlite3 /opt/quants-lab/data/quants.db << 'SQL'
SELECT 
  decision_maker,
  COUNT(*) as decisions,
  COUNT(CASE WHEN action = 'APPROVE' THEN 1 END) as approved,
  COUNT(CASE WHEN action = 'REJECT' THEN 1 END) as rejected
FROM approval_requests
WHERE decided_at > datetime('now', '-30 days')
GROUP BY decision_maker;
SQL

# 4. Escalações
grep -r "ESCALATED" /opt/hummingbot-api/logs/ | wc -l

# 5. Gerar relatório
cat > MONTHLY_AUDIT_$(date +%Y%m).md << 'EOF'
# Monthly Audit Report - April 2024

## Strategies Deployed
- Total: 42
- Approved: 38 (90%)
- Rejected: 4 (10%)

## Performance
- Average Sharpe: 1.87
- Average Return: +8.3%
- Drawdown: -3.2%

## Approvers
- john: 20 decisions (95% approval rate)
- mary: 18 decisions (88% approval rate)

## Escalations
- High risk strategies: 2
- Manual interventions: 1

EOF
```

---

## Matriz de Decisão: Quando Escalar?

```
Situação                              | Escalar Para
----------------------------------------------|------------------
API 500 error                         | Engineer
Strategy Sharpe ratio muito baixo     | Quant Analyst
Operador indeciso sobre aprovação     | Admin
Executor para operando                | Engineer
Portfolio loss > 10% em 1 dia         | Operador → Admin
Credenciais comprometidas             | Admin → Security
Nova política de risco necessária      | Admin → CEO
```

---

## Matriz de Prontidão: Go/No-Go

```
                            | Go ✅ | Caution ⚠️ | No-Go ❌
------|-----|-----|-----
Hummingbot API health       | OK    | Warning   | Error
Quants-Lab API health       | OK    | Warning   | Error
Condor API health           | OK    | Warning   | Error
PostgreSQL connection       | OK    | Slow      | Failed
MongoDB connection          | OK    | Slow      | Failed
EMQX subscribers            | >1    | 1         | 0
Recent approval rate        | >70%  | 50-70%    | <50%
Portfolio loss 24h          | <5%   | 5-10%     | >10%
```

---

## Contatos e Escalação

```
Level 1 (Operacional)
  Operador de Trading
  Email: trading@axodus.io
  Telegram: @operator_bot
  Response Time: < 5 min

Level 2 (Técnico)
  Engineer / DevOps
  Email: engineering@axodus.io
  Slack: #engineering
  Response Time: < 15 min

Level 3 (Estratégico)
  Admin / Governance
  Email: admin@axodus.io
  Slack: #leadership
  Response Time: < 1 hour

On-Call (24/7)
  Operador Senior (rotativo)
  Phone: +55 11 9xxxx-xxxx
  Response Time: < 10 min (crítica)
```

---

**Versão**: 1.0  
**Última Atualização**: 27/04/2026
