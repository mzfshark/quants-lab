# QuantsLab 🚀

Python framework for quantitative trading research with Hummingbot. Built for data collection, backtesting, strategy development, and automated deployment.

## Quick Start

### Installation

```bash
git clone https://github.com/hummingbot/quants-lab.git
cd quants-lab
make install
```

The installer sets up:
- Conda environment (Python 3.12)
- All dependencies
- Database configuration and optional Docker helpers
- Configuration files

### Deploy a Recurring Task

```bash
# 1. Activate environment
conda activate quants-lab

# 2. Start database
make run-db

# 3. Run tasks (Docker - recommended for production)
make run-tasks config=tf_pipeline.yml

# 4. View logs and monitor
make logs-tasks
make ps-tasks

# 5. Stop when done
make stop-tasks
```

**Local development mode:**
```bash
make run-tasks config=tf_pipeline.yml source=1
```

## Key Commands

Type `make` or `make help` to see all commands.

**Installation:**
- `make install` - Full installation
- `make build` - Build Docker image
- `make uninstall` - Remove environment

**Database:**
- `make run-db` - Start services from `docker-compose-db.yml`
- `make stop-db` - Stop MongoDB
- Mongo Express UI: http://localhost:28081 (admin/changeme)

**Tasks:**
- `make run-tasks config=FILE.yml` - Run continuously (Docker)
- `make run-tasks config=FILE.yml source=1` - Run locally
- `make trigger-task task=NAME config=FILE.yml` - Run once
- `make logs-tasks` - View logs
- `make stop-tasks` - Stop all tasks
- `make ps-tasks` - List running tasks

**Configuration:**
- `make list-tasks config=FILE.yml` - List available tasks
- `make validate-config config=FILE.yml` - Validate config

## Human Access Today

Quants-Lab does not ship with a dedicated product-style web dashboard yet. The current human-facing entry points are:

- **CLI**: `python cli.py --help`
- **FastAPI control plane**: `python cli.py serve --config config/tf_pipeline.yml --port 8000`
- **Swagger UI**: http://localhost:8000/docs
- **Jupyter Lab**: `jupyter lab`
- **Optuna Dashboard**: `make launch-optuna`
- **Generated HTML reports**: optimization runs write `report.html` into `app/outputs/...`
- **Mongo Express**: optional helper UI for MongoDB

So yes, there is already a lightweight web-access path via FastAPI `/docs`, but there is not yet a dedicated operations dashboard for strategies, approvals, reports, and deployments.

## Can It Be Extended To A Real Web UI?

Yes. The current architecture is already a good base for that because it has:

- a **FastAPI backend** in `core/tasks/api.py`
- persisted **optimization artifacts** in `app/outputs/`
- persisted **approval/deployment state**
- a task orchestrator and API clients that already separate backend logic from presentation

The natural next step is a small web app that sits on top of the FastAPI layer and exposes:

- task status and trigger controls
- optimization studies and HTML reports
- Condor export/deploy history
- approval queue for manual human review
- bot/runtime health snapshots

## Architecture

```
quants-lab/
├── core/                  # Reusable framework
│   ├── backtesting/       # Backtesting engine + optimizer
│   ├── data_sources/      # Market data integrations (CLOB, AMM, APIs)
│   ├── features/          # Feature engineering & signals
│   └── tasks/             # Task orchestration system
├── app/                   # Application layer
│   ├── tasks/             # Task implementations
│   └── data/              # Application data
├── controllers/           # Trading strategies
├── config/                # Task configurations (YAML)
├── research_notebooks/    # Jupyter notebooks
└── cli.py                # Command-line interface
```

## Configuration Files

Task configurations are YAML files in `config/`:

```yaml
tasks:
  data_collection:
    enabled: true
    task_class: app.tasks.notebook.notebook_task.NotebookTask
    schedule:
      type: interval
      hours: 6
    config:
      notebooks:
        - data_collection/download_candles_all_pairs.ipynb
        - feature_engineering/trend_follower_grid.ipynb
      output_dir: app/outputs/cohort-12
```

## Development

```bash
# Activate environment
conda activate quants-lab

# Run Jupyter for research
jupyter lab

# List available tasks
make list-tasks config=tf_pipeline.yml

# Format code
black --line-length 130 .
isort --profile black --line-length 130 .
```

## Database Access

- **MongoDB**: `mongodb://admin:admin@localhost:27017/quants_lab`
- **Mongo Express UI**: http://localhost:28081 (admin/changeme)
- **Config**: All settings in `.env` file

## Data Sources

- **CLOB**: Order books, trades, candles, funding rates
- **AMM**: DEX liquidity and pool data
- **GeckoTerminal**: Multi-network OHLCV data
- **CoinGecko**: Market data and stats

## Troubleshooting

**Database connection issues:**
```bash
make run-db
docker ps  # Verify containers running
```

If you already run MongoDB elsewhere, point `.env` to that service with `MONGO_URI` and use `docker-compose-db.yml` only for helper services like `mongo-express`.

**Task failures:**
```bash
make logs-tasks  # View logs
make validate-config config=YOUR_CONFIG.yml
```

**Port conflicts:**
Edit `docker-compose-db.yml` if a local MongoDB or helper UI port is already in use.

## Support

- 📚 Documentation: See `CLAUDE.md` for dev guidelines
- 🐛 Issues: GitHub issues
- 💡 Contributing: Fork and submit PRs

---

**Happy Trading! 🚀📈**
