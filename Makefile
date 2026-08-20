.PHONY: install dev api dispatcher worker demo test test-unit test-integration test-external lint typecheck web-install web-build e2e migrate

install:
	uv sync --all-groups
	pnpm install

api:
	uv run uvicorn apps.api.agent_fleet_api.main:app --reload --host 0.0.0.0 --port 8000

dispatcher:
	uv run python -m services.dispatcher.main

worker:
	uv run python -m services.worker.main --config $${AGENT_FLEET_WORKER_CONFIG:-infra/compose/config/worker-a.yaml}

dev:
	docker compose -f infra/compose/docker-compose.yml up --build

demo:
	./scripts/demo.sh

migrate:
	uv run alembic upgrade head

test: test-unit test-integration

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-external:
	uv run pytest -m external

lint:
	uv run ruff check .
	uv run ruff format --check .
	pnpm --dir apps/web lint

typecheck:
	uv run mypy apps services packages scripts tests
	pnpm --dir apps/web typecheck

web-install:
	pnpm install

web-build:
	pnpm --dir apps/web build

e2e:
	pnpm --dir apps/web test:e2e
