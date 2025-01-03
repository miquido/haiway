SHELL := sh
.ONESHELL:
.SHELLFLAGS := -eu -c
.DELETE_ON_ERROR:

SOURCES_PATH := src

# load environment config from .env if able
-include .env

ifndef UV_VERSION
	UV_VERSION := 0.5.14
endif

.PHONY: uv_check venv sync update format lint run docker_run

# Check installed UV version and install if needed
uv_check:
	@echo 'Checking uv version...'

	# Install if not present
	@if ! command -v uv > /dev/null; then \
		echo '...installing uv...'; \
		curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh; \
		if [ $$? -ne 0 ]; then \
			echo "...installing uv failed!"; \
			exit 1; \
		fi; \
	fi

	# Check version and update if needed
	@if command -v uv > /dev/null; then \
		CURRENT_VERSION=$$(uv --version | head -n1 | cut -d" " -f2); \
		if [ "$$(printf "%s\n%s" "$(UV_VERSION)" "$$CURRENT_VERSION" | sort -V | head -n1)" != "$(UV_VERSION)" ]; then \
			echo '...updating uv...'; \
			curl -LsSf https://github.com/astral-sh/uv/releases/download/$(UV_VERSION)/uv-installer.sh | sh; \
			if [ $$? -ne 0 ]; then \
				echo "...updating uv failed!"; \
				exit 1; \
			fi; \
		else \
			echo '...uv version is up-to-date!'; \
		fi; \
	fi

# Setup virtual environment for local development.
venv: uv_check
	@echo '# Preparing development environment...'
	@echo '...cloning .env...'
	@cp -n ./config/.env.example ./.env || :
	@echo '...preparing git hooks...'
	@cp -n ./config/pre-push ./.git/hooks/pre-push || :
	@echo '...preparing venv...'
	@uv sync --all-groups --all-extras --frozen --reinstall
	@echo '...development environment ready! Activate venv using `. ./.venv/bin/activate`.'

# Sync environment with uv based on constraints
sync: uv_check
	@echo '# Synchronizing dependencies...'
	@uv sync --all-groups --all-extras --frozen
	@echo '...finished!'

# Update and lock dependencies from pyproject.toml
update:
	@echo '# Updating dependencies...'
	@uv sync --all-groups --all-extras --upgrade
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

	# Run all services locally using docker-compose.
docker_run:
	@docker compose up --force-recreate --build server
