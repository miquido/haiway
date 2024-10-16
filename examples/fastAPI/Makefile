SHELL := sh
.ONESHELL:
.SHELLFLAGS := -eu -c
.DELETE_ON_ERROR:

SOURCES_PATH := src

# load environment config from .env if able
-include .env

ifndef PYTHON_ALIAS
	PYTHON_ALIAS := python
endif

ifndef INSTALL_OPTIONS
	INSTALL_OPTIONS := .[dev]
endif

ifndef UV_VERSION
	UV_VERSION := 0.4.22
endif

.PHONY: venv sync lock update format lint test run

# Setup virtual environment for local development.
venv:
	@echo '# Preparing development environment...'
	@echo '...installing uv...'
	@curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh
	@echo '...preparing venv...'
	@$(PYTHON_ALIAS) -m venv .venv --prompt="VENV[DEV]" --clear --upgrade-deps
	@. ./.venv/bin/activate && pip install --upgrade pip && uv pip install --editable $(INSTALL_OPTIONS) --constraint constraints
	@echo '...development environment ready! Activate venv using `. ./.venv/bin/activate`.'

# Sync environment with uv based on constraints
sync:
	@echo '# Synchronizing dependencies...'
	@$(if $(findstring $(UV_VERSION), $(shell uv --version | head -n1 | cut -d" " -f2)), , @echo '...updating uv...' && curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh)
	@uv pip install --editable $(INSTALL_OPTIONS) --constraint constraints
	@echo '...finished!'

# Generate a set of locked dependencies from pyproject.toml
lock:
	@echo '# Locking dependencies...'
	@uv pip compile pyproject.toml -o constraints --all-extras
	@echo '...finished!'

# Update and lock dependencies from pyproject.toml
update:
	@echo '# Updating dependencies...'
	@$(if $(shell printf '%s\n%s\n' "$(UV_VERSION)" "$$(uv --version | head -n1 | cut -d' ' -f2)" | sort -V | head -n1 | grep -q "$(UV_VERSION)"), , @echo '...updating uv...' && curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh)
	# @$(if $(findstring $(UV_VERSION), $(shell uv --version | head -n1 | cut -d" " -f2)), , @echo '...updating uv...' && curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh)
	@uv --no-cache pip compile pyproject.toml -o constraints --all-extras --upgrade
	@uv pip install --editable $(INSTALL_OPTIONS) --constraint constraints
	@echo '...finished!'

# Run formatter.
format:
	@ruff check --quiet --fix $(SOURCES_PATH)
	@ruff format --quiet $(SOURCES_PATH)

# Run linters and code checks.
lint:
	@bandit -r $(SOURCES_PATH)
	@ruff check $(SOURCES_PATH)
	@pyright --project ./

# Run the server
run:
	@python -m server