-include .env
export

ROOT := $(abspath .)
API := $(ROOT)/apps/api
UV := uv --directory $(API) run

.PHONY: install test lint classify eval compare compare-models promote up down smoke web-types

install:
	uv --directory $(API) sync

test:
	$(UV) pytest

lint:
	$(UV) ruff format --check . && $(UV) ruff check .

classify:
	$(if $(SET),,$(error SET is required, e.g. make classify SET=case))
	@echo "provider=$${LLM_PROVIDER:-auto} model=$${LLM_MODEL:-claude-haiku-4-5} temperature=$${LLM_TEMPERATURE:-0} set=$(SET)"
	@$(UV) python -c "import json; from pitz_pulse.config import DEFAULT_APP_ROOT; from pitz_pulse.runs import SETS, repo_root; print(len(json.load(open(repo_root(DEFAULT_APP_ROOT) / SETS['$(SET)']))), 'messages; retries can add calls')"
	$(UV) python -m pitz_pulse.batch --set $(SET) $(if $(SUFFIX),--suffix $(SUFFIX)) $(if $(filter 1,$(FORCE)),--force)

eval:
	$(if $(RUN),,$(error RUN is required, e.g. make eval RUN=case__v1__mock__mock))
	$(UV) python -m pitz_pulse.evaluate --run $(RUN) $(if $(COMPARE),--compare $(COMPARE)) $(if $(THRESHOLD),--threshold $(THRESHOLD))

compare:
	$(if $(RUN),,$(error RUN is required, e.g. make compare RUN=... COMPARE=...))
	$(if $(COMPARE),,$(error COMPARE is required, e.g. make compare RUN=... COMPARE=...))
	$(UV) python -m pitz_pulse.evaluate --run $(RUN) $(if $(COMPARE),--compare $(COMPARE)) $(if $(THRESHOLD),--threshold $(THRESHOLD))

compare-models:
	$(if $(MODELS),,$(error MODELS is required, e.g. make compare-models MODELS="claude-haiku-4-5 claude-sonnet-5" LLM_PROVIDER=mock))
	$(if $(LLM_PROVIDER),,$(error LLM_PROVIDER is required here (no auto-selection); use LLM_PROVIDER=mock or LLM_PROVIDER=anthropic_api))
	$(if $(filter mock,$(LLM_PROVIDER)),$(if $(word 2,$(MODELS)),$(error mock ignores LLM_MODEL and always writes model=mock: pass exactly one MODELS entry, e.g. make compare-models MODELS=claude-haiku-4-5 LLM_PROVIDER=mock),),)
	@echo "provider=$(LLM_PROVIDER) models: $(MODELS)"
	@echo "2 x (12+18) messages per model; retries can add calls"
	$(if $(filter mock,$(LLM_PROVIDER)),,$(if $(filter 1,$(CONFIRM)),,$(error non-mock provider $(LLM_PROVIDER): re-run with CONFIRM=1, e.g. make compare-models MODELS="$(MODELS)" LLM_PROVIDER=$(LLM_PROVIDER) CONFIRM=1)))
	$(UV) python -m pitz_pulse.preflight --provider $(LLM_PROVIDER) --models $(MODELS)
	$(foreach model,$(MODELS),$(MAKE) classify SET=case SUFFIX=cmp FORCE=$(FORCE) LLM_MODEL=$(model) $(if $(filter claude-sonnet-5,$(model)),LLM_TEMPERATURE=none) && $(MAKE) classify SET=edge SUFFIX=cmp FORCE=$(FORCE) LLM_MODEL=$(model) $(if $(filter claude-sonnet-5,$(model)),LLM_TEMPERATURE=none) &&) true
	$(UV) python -m pitz_pulse.model_compare --runs $$($(UV) python -c "from pitz_pulse.config import ACTIVE_PROMPT_VERSION as V; from pitz_pulse.runs import run_stem as R; print(' '.join(R(s, V, '$(LLM_PROVIDER)', m, 'cmp') for m in '$(if $(filter mock,$(LLM_PROVIDER)),mock,$(MODELS))'.split() for s in ('case', 'edge')))")

promote:
	$(if $(RUN),,$(error RUN is required, e.g. make promote RUN=case__v1__anthropic_api__claude-haiku-4-5))
	$(UV) python -m pitz_pulse.promote --run $(RUN) $(if $(filter 1,$(ALLOW_MOCK)),--allow-mock) $(if $(filter 1,$(FORCE)),--force)

up:
	docker compose up --build -d --wait

down:
	docker compose down

smoke:
	./scripts/smoke.sh

web-types:
	@db=$$(mktemp) && out=$$(mktemp) && trap 'rm -f $$db $$out' EXIT && \
	LLM_PROVIDER=mock API_KEY=web-types DB_PATH=$$db $(UV) python -c \
		"import json; from pitz_pulse.api import create_app; print(json.dumps(create_app().openapi(), indent=2))" \
		> $$out && mv $$out apps/web/openapi.json
	npm --prefix apps/web run gen:types
