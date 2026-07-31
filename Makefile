.PHONY: install install-all run test lint format typecheck web api-start api-stop api-restart api-status futu-start futu-stop financial-pull-install financial-pull-now financial-dog-pull-install financial-dog-pull-now docker-up docker-down bundle

install:
	python -m pip install -e ".[dev]"

install-all:
	python -m pip install -e ".[cn-data,futu,ml,db,dev]"

run:
	uvicorn alphapilot.main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest

lint:
	ruff check src tests

format:
	ruff format src tests
	ruff check --fix src tests

typecheck:
	mypy src/alphapilot

web:
	cd apps/web && npm install && npm run dev

api-start:
	./scripts/start_api_launchd.sh

api-stop:
	./scripts/stop_api_launchd.sh

api-restart:
	./scripts/restart_api_launchd.sh

api-status:
	./scripts/status_api_launchd.sh

futu-start:
	./scripts/start_futu_opend.sh

futu-stop:
	./scripts/stop_futu_opend.sh

financial-pull-install:
	./scripts/install_financial_pull_launchd.sh

financial-pull-now:
	.venv/bin/python scripts/pull_financial_snapshot.py --ssh-target root@47.93.234.51 --remote-root /opt/alphapilot-s2 --target-db data/alphapilot.db --snapshot-dir data/phase3-s2

financial-dog-pull-install:
	./scripts/install_financial_dog_pull_launchd.sh

financial-dog-pull-now:
	.venv/bin/python scripts/pull_financial_snapshot.py --ssh-target root@206.237.18.80 --remote-root /opt/alphapilot-s2-dog --remote-exporter /opt/alphapilot-s2-dog/export-financial-snapshot.sh --target-db data/alphapilot.db --snapshot-dir data/phase3-s2-dog

docker-up:
	docker compose up --build

docker-down:
	docker compose down

bundle:
	git bundle create AlphaPilot-AI.bundle --all
