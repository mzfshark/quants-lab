# Trinity Integration Guide

Quants-Lab exposes a headless API that Trinity can use as a strategy validation, backtesting, and optimization engine.

## Recommended Mode

Start the API without background tasks:

```bash
python cli.py serve --api-only --port 8075
```

```powershell
# (Windows / PowerShell)
Set-Location Z:\opt\quants-lab
python cli.py serve --api-only --port 8075
Start-Process http://127.0.0.1:8075/docs
```

Open the interactive docs:

- `http://localhost:8075/docs`

## Canonical Example Files

- Manifest: `examples/trinity/strategy_manifest_macd_bb.json`
- Backtest payload: `examples/trinity/backtest_request_macd_bb.json`
- Optimization payload: `examples/trinity/optimization_request_macd_bb.json`

These are the reference payloads for Trinity and external clients such as Axodus.

## Contract Summary

### 1. Validate a manifest

`POST /api/v1/strategies/validate`

Request body:

- raw `StrategyManifest`

```bash
curl -X POST "http://localhost:8075/api/v1/strategies/validate" \
  -H "Content-Type: application/json" \
  --data @examples/trinity/strategy_manifest_macd_bb.json
```

```powershell
# (Windows / PowerShell) use curl.exe (not the PowerShell alias)
curl.exe -X POST "http://127.0.0.1:8075/api/v1/strategies/validate" `
  -H "Content-Type: application/json" `
  --data "@examples/trinity/strategy_manifest_macd_bb.json"
```

### 2. Run a single-candidate backtest

`POST /api/v1/backtests`

Request body:

- `manifest`
- `candidate_params`
- `export_artifacts`

```bash
curl -X POST "http://localhost:8075/api/v1/backtests" \
  -H "Content-Type: application/json" \
  --data @examples/trinity/backtest_request_macd_bb.json
```

```powershell
curl.exe -X POST "http://127.0.0.1:8075/api/v1/backtests" `
  -H "Content-Type: application/json" `
  --data "@examples/trinity/backtest_request_macd_bb.json"
```

### 3. Run an optimization job

`POST /api/v1/optimizations`

Request body:

- `manifest`

```bash
curl -X POST "http://localhost:8075/api/v1/optimizations" \
  -H "Content-Type: application/json" \
  --data @examples/trinity/optimization_request_macd_bb.json
```

```powershell
curl.exe -X POST "http://127.0.0.1:8075/api/v1/optimizations" `
  -H "Content-Type: application/json" `
  --data "@examples/trinity/optimization_request_macd_bb.json"
```

### 4. Poll job status

`GET /api/v1/jobs/{job_id}`

```bash
curl "http://localhost:8075/api/v1/jobs/<job_id>"
```

```powershell
curl.exe "http://127.0.0.1:8075/api/v1/jobs/<job_id>"
```

### 5. Fetch study results and report artifacts

```bash
curl "http://localhost:8075/api/v1/studies/<study_name>"
curl "http://localhost:8075/api/v1/reports/<study_name>"
curl "http://localhost:8075/api/v1/approvals?study_name=<study_name>"
```

```powershell
curl.exe "http://127.0.0.1:8075/api/v1/studies/<study_name>"
curl.exe "http://127.0.0.1:8075/api/v1/reports/<study_name>"
curl.exe "http://127.0.0.1:8075/api/v1/approvals?study_name=<study_name>"
```

## Trinity Conventions

For reliable automation, Trinity should follow these conventions:

1. Always generate a unique `study.name` per run unless intentionally continuing an Optuna study.
2. Always send `controller_type` explicitly when known.
3. Use `strategies/validate` before `backtests` or `optimizations`.
4. Treat `approval_request.status` as the deploy gate:
   - `pending`: candidate passed validation and awaits human approval
   - `blocked`: optimization completed, but walk-forward validation failed
5. Read report artifacts from the `reports` endpoint instead of scraping the filesystem.

## Suggested Trinity Flow

1. Build a `StrategyManifest`
2. `POST /api/v1/strategies/validate`
3. If valid, either:
   - `POST /api/v1/backtests` for a single candidate
   - `POST /api/v1/optimizations` for a full parameter search
4. Poll `/api/v1/jobs/{job_id}`
5. Read `/api/v1/reports/{study_name}`
6. If the approval status is `pending`, let a human or policy layer decide on deployment

## Notes

- This contract is intentionally headless so the same API can be used by Trinity and Axodus.
- The bundled examples use `macd_bb_v1` because it is already wired into the repo and valid for end-to-end testing.
- Example payloads are valid shape references, not guaranteed profitable strategies.
