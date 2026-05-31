# predmarkbot

Kalshi prediction-market trading bot. Single-process asyncio Python service.
Currently only runs against [Kalshi's demo API](https://demo-api.kalshi.co/).

## Quickstart (local)

```bash
# Enter the dev shell (provides python 3.12, uv, sqlite)
direnv allow

# Install deps
uv sync

# Run smoke self-checks against Kalshi demo
export KALSHI_KEY_ID=<your-key-id>
export KALSHI_PRIVATE_KEY_PATH=/path/to/private_key.pem
uv run python -m predmarkbot smoke --config ./config.yaml

# Start the bot in shadow mode (no orders placed; records what it *would* trade)
uv run python -m predmarkbot run --config ./config.yaml

# Inspect state
uv run python -m predmarkbot status --config ./config.yaml
sqlite3 ./state.db "SELECT * FROM shadow_intents ORDER BY ts DESC LIMIT 10"
```

## Modes

- `shadow` — strategy runs, intents recorded, **no orders placed**. Run for ≥1 week.
- `demo` — orders placed against Kalshi demo (fake money).
- `prod` — real money. Requires `prod_confirmed: true` in config as a safety gate.

## Testing

```bash
uv run pytest                              # unit tests only
uv run pytest -m integration               # hits Kalshi demo (network)
uv run ruff check src tests
uv run mypy src
```

## Architecture

See [docs/superpowers/specs/2026-05-30-kalshi-bot-design.md](docs/superpowers/specs/2026-05-30-kalshi-bot-design.md).

## Deployment

See Plan 2 (containerization + homelab Kubernetes via ArgoCD) — to be written.
