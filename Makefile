-include .env
export

ROOT := $(abspath .)
API := $(ROOT)/apps/api
UV := uv --directory $(API) run

.PHONY: install test lint classify eval compare promote up down smoke

install:
	uv --directory $(API) sync

test:
	$(UV) pytest

lint:
	$(UV) ruff format --check . && $(UV) ruff check .

classify:
	$(if $(SET),,$(error SET is required, e.g. make classify SET=case))
	@echo "provider=$${LLM_PROVIDER:-auto} model=$${LLM_MODEL:-claude-haiku-4-5} set=$(SET)"
	@$(UV) python -c "import json; from pitz_pulse.config import DEFAULT_APP_ROOT; from pitz_pulse.runs import SETS, repo_root; print(len(json.load(open(repo_root(DEFAULT_APP_ROOT) / SETS['$(SET)']))), 'calls')"
	$(UV) python -m pitz_pulse.batch --set $(SET) $(if $(SUFFIX),--suffix $(SUFFIX)) $(if $(FORCE),--force)

eval:
	$(if $(RUN),,$(error RUN is required, e.g. make eval RUN=case__v1__mock__mock))
	$(UV) python -m pitz_pulse.evaluate --run $(RUN) $(if $(COMPARE),--compare $(COMPARE)) $(if $(THRESHOLD),--threshold $(THRESHOLD))

compare:
	$(if $(RUN),,$(error RUN is required, e.g. make compare RUN=... COMPARE=...))
	$(if $(COMPARE),,$(error COMPARE is required, e.g. make compare RUN=... COMPARE=...))
	$(UV) python -m pitz_pulse.evaluate --run $(RUN) $(if $(COMPARE),--compare $(COMPARE)) $(if $(THRESHOLD),--threshold $(THRESHOLD))

promote:
	$(if $(RUN),,$(error RUN is required, e.g. make promote RUN=case__v1__anthropic_api__claude-haiku-4-5))
	$(UV) python -m pitz_pulse.promote --run $(RUN) $(if $(filter 1,$(ALLOW_MOCK)),--allow-mock) $(if $(filter 1,$(FORCE)),--force)

up:
	docker compose up --build -d

down:
	docker compose down

smoke:
	./scripts/smoke.sh
