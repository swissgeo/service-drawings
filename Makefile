SHELL = /bin/bash

.DEFAULT_GOAL := help

SERVICE_NAME := service-drawings
HTTP_PORT ?= 8000

CURRENT_DIR := $(shell pwd)

# Docker metadata
GIT_HASH := $(shell git rev-parse HEAD)
GIT_HASH_SHORT := $(shell git rev-parse --short HEAD)
GIT_BRANCH := $(shell git symbolic-ref HEAD --short 2>/dev/null)
GIT_DIRTY := $(shell git status --porcelain)
GIT_TAG := $(shell git describe --tags || echo "no version info")
AUTHOR := $(USER)

# Commands
UV_RUN := uv run
PYTHON := $(UV_RUN) python3
TEST := $(UV_RUN) pytest
RUFF := $(UV_RUN) ruff
TY := $(UV_RUN) ty
PRE_COMMIT := $(UV_RUN) pre-commit
FASTAPI := $(UV_RUN) fastapi
UVICORN := $(UV_RUN) uvicorn

# Docker variables?
DOCKER_REGISTRY := 074597099015.dkr.ecr.eu-central-1.amazonaws.com
DOCKER_IMG_LOCAL_TAG := $(DOCKER_REGISTRY)/swissgeo/$(SERVICE_NAME):local-$(USER)-$(GIT_HASH_SHORT)

# AWS variables
AWS_REGION := eu-central-1

# Env file for dockerrun, defaults to .env.default / .env
ENV_FILE ?= $(if $(wildcard .env),.env,.env.default)
# export the env file so that uv picks it up in all recipes below
export UV_ENV_FILE := $(ENV_FILE)

-include $(ENV_FILE)

CONTAINER_LOGGING_CONFIG := /app/logging-config.yaml
LOGGING_CONFIG_FILE ?= logging-otel-config.yaml

.env:
	cp .env.default .env


.PHONY: git-info
git-info: ## Print the current version information
	@echo "GIT_HASH=$(GIT_HASH)"
	@echo "GIT_HASH_SHORT=$(GIT_HASH_SHORT)"
	@echo "GIT_BRANCH=$(GIT_BRANCH)"
	@echo "GIT_DIRTY=$(GIT_DIRTY)"
	@echo "GIT_TAG=$(GIT_TAG)"
	@echo "AUTHOR=$(AUTHOR)"
	@echo "DOCKER_IMG_LOCAL_TAG=$(DOCKER_IMG_LOCAL_TAG)"


.PHONY: ci
ci: .env
	# Create virtual env with all packages for development using the lockfile
	uv sync --frozen


.PHONY: setup
setup: .env start-moto start-otel ## Create virtualenv with all packages for development
	uv sync
	$(PRE_COMMIT) install
	# Start a new shell with the virtualenv activated and the .env file loaded into the environment
	# variables. The latter is required for FastAPI which reads the settings from the environment variables
	uv run $$SHELL


.PHONY: sync
sync: ## Sync dependencies with lockfile
	uv sync --frozen


.PHONY: format
format: ## Call ruff format to make sure your code is easier to read and respects some conventions.
	$(RUFF) format
	$(RUFF) check --select I --fix


.PHONY: ci-check-format
ci-check-format: format ## Check the format (CI)
	@if [[ -n `git status --porcelain --untracked-files=no` ]]; then \
	 	>&2 echo "ERROR: the following files are not formatted correctly"; \
	 	>&2 echo "'git status --porcelain' reported changes in those files after a 'make format' :"; \
		>&2 git status --porcelain --untracked-files=no; \
		exit 1; \
	fi


.PHONY: serve
serve: ## Serve the application for development
	${FASTAPI} dev --port ${HTTP_PORT}


.PHONY: dockerlogin
dockerlogin: ## Login to the AWS Docker Registry (ECR)
	aws --profile swisstopo-swissgeo-builder ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(DOCKER_REGISTRY)


.PHONY: dockerbuild
dockerbuild: ## Create a docker image
	docker build \
		--build-arg GIT_HASH="$(GIT_HASH)" \
		--build-arg GIT_BRANCH="$(GIT_BRANCH)" \
		--build-arg GIT_DIRTY="$(GIT_DIRTY)" \
		--build-arg VERSION="$(GIT_TAG)" \
		--build-arg AUTHOR="$(AUTHOR)" -t $(DOCKER_IMG_LOCAL_TAG) .


.PHONY: dockerpush
dockerpush: dockerbuild ## Push to the docker registry
	set -o pipefail; \
	docker push $(DOCKER_IMG_LOCAL_TAG) 2>&1 | grep --color=always -E "tag invalid: .*|$$"


.PHONY: dockerrun
dockerrun: dockerbuild ## Run the locally built docker image
	docker run \
		-it \
		--env LOGGING_CONFIG_FILE=$(CONTAINER_LOGGING_CONFIG) \
		--env-file=${ENV_FILE} \
		--net=host \
		-v $(PWD)/$(LOGGING_CONFIG_FILE):$(CONTAINER_LOGGING_CONFIG):ro \
		$(DOCKER_IMG_LOCAL_TAG) --log-config $(CONTAINER_LOGGING_CONFIG) --port $(HTTP_PORT)


.PHONY: lint
lint: ## Run the linter and type checker on the code base
	$(RUFF) check
	$(TY) check


.PHONY: check
check: lint test ## Run lint + typecheck + tests (full CI gate locally)
	@echo "All checks passed"


.PHONY: test-ci
test-ci: ## Run tests in the CI
	# NOTE on the CI we do not fail the build if the coverage is below 100% because we want to be
	# able to merge PRs even if they decrease the coverage. The coverage report will be used in
	# codecov.io to track the coverage over time and to check if it decreases or not. Also
	# the coverage report is only loaded if make test-ci is successful.
	$(TEST) --cov --cov-branch --cov-report=xml:coverage.xml


.PHONY: test
test: ## Run tests locally
	$(TEST) --cov --cov-branch --cov-report=term --cov-report=html


docker-network:
	@if ! docker network inspect $(SERVICE_NAME)-network >/dev/null 2>&1; then \
		echo "Creating network $(SERVICE_NAME)-network"; \
		docker network create $(SERVICE_NAME)-network; \
	else \
		echo "Network $(SERVICE_NAME)-network already exists"; \
	fi


.PHONY: start-moto
start-moto: docker-network ## Run moto server locally and initialize resources (S3)
	# reuse existing container if present, otherwise create it via compose
	docker inspect moto-server >/dev/null 2>&1 && docker start moto-server || docker compose --env-file=${ENV_FILE} up -d moto-server
	# run one-shot init container to create S3 bucket
	docker compose --env-file=${ENV_FILE} up --remove-orphans init-s3


.PHONY: stop-moto
stop-moto: ## Stop the moto server container
	docker stop moto-server


.PHONY: start-otel
start-otel: docker-network ## Run otel collector and jaeger trace analyzer locally
	docker compose up -d jaeger
	docker compose up otel-collector


.PHONY: stop-otel
stop-otel: ## Stop the otel collector and jaeger trace analyzer
	docker compose down jaeger
	docker compose down otel-collector


.PHONY: help
help: ## Display this help
# automatically generate the help page based on the documentation after each make target
# from https://gist.github.com/prwhite/8168133
	@awk 'BEGIN {FS = ":.*##"} \
	/^[a-zA-Z0-9_.-]+:.*##/ { printf "%-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST) \
	| sort \
	| awk 'BEGIN { printf "\nUsage:\n  make \033[36m<target>\033[0m\n\n" } \
	{ printf "  \033[36m%-15s\033[0m %s\n", $$1, substr($$0, index($$0,$$2)) }'
