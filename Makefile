# Chimera — developer tasks. Run `make help` for the list.
# Works on Linux / macOS / WSL (POSIX make). On native Windows, run the underlying
# `uv run ...` commands directly, or use `make` inside WSL / Git Bash.

.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install check lint type test fmt cov docs clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Sync the dev environment (uv)
	$(UV) sync --extra dev

check: lint type test ## The full quality gate — run this before every PR

lint: ## Ruff lint (no changes)
	$(UV) run --no-sync ruff check .

type: ## mypy --strict on the package
	$(UV) run --no-sync mypy chimera

test: ## Run the test suite (quiet)
	$(UV) run --no-sync pytest -q

fmt: ## Auto-fix lint + format
	$(UV) run --no-sync ruff check --fix .
	$(UV) run --no-sync ruff format .

cov: ## Test suite with a coverage report
	$(UV) run --no-sync pytest --cov=chimera --cov-report=term-missing

docs: ## Build the docs site (requires the `docs` extra)
	$(UV) run --no-sync mkdocs build

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build site
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
