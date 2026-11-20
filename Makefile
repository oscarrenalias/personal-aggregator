VERSION := $(shell git describe --tags --always --dirty)

DATASET ?= $(shell ls -t evals/clusterer_merge/datasets/*.jsonl 2>/dev/null | head -1)
LABELS  ?= evals/clusterer_merge/labels/merge-labels.csv
SHEET   ?= evals/results/merge-label-sheet.xlsx
REPEAT  ?= 1
PROMPTFOO_PYTHON := ./evals/promptfoo-python.sh

.PHONY: build up down logs version openapi \
        eval-install eval-merge-export eval-merge eval-merge-report \
        eval-merge-smoke eval-merge-sheet eval-merge-import eval-view

build:
	scripts/build-images.sh

up:
	docker compose -f docker-compose.prod.yml up -d

down:
	docker compose -f docker-compose.prod.yml down

logs:
	docker compose -f docker-compose.prod.yml logs -f

version:
	@echo $(VERSION)

openapi:
	DATABASE_URL=postgresql://placeholder@localhost/aggregator \
	uv run --package aggregator-api python -c \
	  "import json; from aggregator_api.app import app; open('docs/openapi.json','w').write(json.dumps(app.openapi(), indent=2))"

eval-install:
	npm ci --prefix evals

eval-merge-export:
	.claude/skills/prod-db/prod-db.sh psql -qAt -f - \
	  < evals/clusterer_merge/export.sql \
	  | $(PROMPTFOO_PYTHON) evals/clusterer_merge/build_dataset.py \
	      --out evals/clusterer_merge/datasets/pairs-$$(date -u +%F).jsonl

eval-merge:
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval TESTS_OUT := evals/results/clusterer-merge-tests-$(TIMESTAMP).yaml)
	$(eval RESULTS_OUT := evals/results/clusterer-merge-$(TIMESTAMP).json)
	$(eval _EXCLUDE_ARGS := $(foreach t,$(EXCLUDE_TAGS),--exclude-tag $(t)))
	$(eval _ONLY_ARGS    := $(foreach t,$(ONLY_TAGS),--only-tag $(t)))
	$(eval _FILTER_ARG   := $(if $(FILTER),--filter-pattern $(FILTER),))
	$(eval _NOCACHE_ARG  := $(if $(NOCACHE),--no-cache,))
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/render_tests.py \
	  --dataset $(DATASET) --labels $(LABELS) --out $(TESTS_OUT) \
	  $(_EXCLUDE_ARGS) $(_ONLY_ARGS)
	PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	npx --prefix evals promptfoo eval \
	  -c evals/clusterer_merge/promptfooconfig.yaml \
	  --tests $(TESTS_OUT) \
	  -o $(RESULTS_OUT) \
	  --repeat $(REPEAT) \
	  $(_FILTER_ARG) \
	  $(_NOCACHE_ARG)
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/report.py $(RESULTS_OUT) \
	  $(_EXCLUDE_ARGS) $(_ONLY_ARGS)

eval-merge-report:
	$(eval _LATEST := $(shell ls -t evals/results/clusterer-merge-*.json 2>/dev/null | head -1))
	$(eval _EXCLUDE_ARGS := $(foreach t,$(EXCLUDE_TAGS),--exclude-tag $(t)))
	$(eval _ONLY_ARGS    := $(foreach t,$(ONLY_TAGS),--only-tag $(t)))
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/report.py $(_LATEST) \
	  $(_EXCLUDE_ARGS) $(_ONLY_ARGS)

eval-merge-smoke:
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval RESULTS_OUT := evals/results/clusterer-merge-smoke-$(TIMESTAMP).json)
	PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	npx --prefix evals promptfoo eval \
	  -c evals/clusterer_merge/smoke.promptfooconfig.yaml \
	  -o $(RESULTS_OUT) || true
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/report.py $(RESULTS_OUT)

eval-merge-sheet:
	$(eval _LIMIT_ARG := $(if $(LIMIT),--limit $(LIMIT),))
	$(eval _SEED_ARG  := $(if $(SEED),--seed $(SEED),))
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/label_sheet.py export \
	  --dataset $(DATASET) --labels $(LABELS) --out $(SHEET) \
	  --only-unlabeled $(_LIMIT_ARG) $(_SEED_ARG)

eval-merge-import:
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/label_sheet.py import \
	  --sheet $(SHEET) --labels $(LABELS)

eval-view:
	cd evals && npx promptfoo view
