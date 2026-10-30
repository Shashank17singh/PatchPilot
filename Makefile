.PHONY: install test lint format typecheck web bench all

install:
	uv sync --all-extras

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

web:
	uv run streamlit run webapp/app.py

bench:
	uv run patchpilot-bench

all: format typecheck test
