.PHONY: install install-all run test lint format typecheck web docker-up docker-down bundle

install:
	python -m pip install -e ".[dev]"

install-all:
	python -m pip install -e ".[cn-data,futu,ml,db,dev]"

run:
	uvicorn alphapilot.main:app --reload --host 0.0.0.0 --port 8000

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

docker-up:
	docker compose up --build

docker-down:
	docker compose down

bundle:
	git bundle create AlphaPilot-AI.bundle --all
