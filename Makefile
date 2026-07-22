.PHONY: install install-all run test lint format typecheck web api-start api-stop api-restart api-status futu-start futu-stop docker-up docker-down bundle

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

docker-up:
	docker compose up --build

docker-down:
	docker compose down

bundle:
	git bundle create AlphaPilot-AI.bundle --all
