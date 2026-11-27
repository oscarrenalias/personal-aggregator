VERSION := $(shell git describe --tags --always --dirty)

DATASET         ?= $(shell ls -t evals/clusterer_merge/datasets/*.jsonl 2>/dev/null | head -1)
LABELS          ?= evals/clusterer_merge/labels/merge-labels.csv
SHEET           ?= evals/results/merge-label-sheet.xlsx
RANK_DATASET    ?= $(shell ls -t evals/summarize_rank/datasets/*.jsonl 2>/dev/null | head -1)
REPEAT          ?= 1
MAXCONCURRENCY  ?= 8
PROMPTFOO_PYTHON := ./evals/promptfoo-python.sh
# Used for scripts that require openpyxl (label_sheet.py); not invoked by promptfoo
UV_PYTHON := uv run --all-packages --with openpyxl python

.PHONY: build up down logs version openapi \
        eval-install eval-venv-sync eval-cache-clear \
        eval-merge-export eval-merge eval-merge-report \
        eval-merge-smoke eval-merge-sheet eval-merge-import eval-view \
        eval-rank-export eval-rank eval-rank-report eval-rank-smoke

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

eval-venv-sync:
	uv sync --all-packages --inexact

eval-cache-clear:
	rm -rf evals/.cache/litellm

eval-merge-export:
	.claude/skills/prod-db/prod-db.sh psql -qAt -f - \
	  < evals/clusterer_merge/export.sql \
	  | $(PROMPTFOO_PYTHON) evals/clusterer_merge/build_dataset.py \
	      --out evals/clusterer_merge/datasets/pairs-$$(date -u +%F).jsonl

eval-merge: eval-venv-sync
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval TESTS_OUT := evals/results/clusterer-merge-tests-$(TIMESTAMP).yaml)
	$(eval RESULTS_OUT := evals/results/clusterer-merge-$(TIMESTAMP).json)
	$(eval _EXCLUDE_ARGS := $(foreach t,$(EXCLUDE_TAGS),--exclude-tag $(t)))
	$(eval _ONLY_ARGS    := $(foreach t,$(ONLY_TAGS),--only-tag $(t)))
	$(eval _FILTER_ARG   := $(if $(FILTER),--filter-pattern $(FILTER),))
	$(eval _NOCACHE_ARG  := $(if $(NOCACHE),--no-cache,))
	$(eval _EVAL_NO_CACHE := $(if $(NOCACHE),EVAL_NO_CACHE=1,))
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/render_tests.py \
	  --dataset $(DATASET) --labels $(LABELS) --out $(TESTS_OUT) \
	  $(_EXCLUDE_ARGS) $(_ONLY_ARGS)
	$(_EVAL_NO_CACHE) PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	PROMPTFOO_FAILED_TEST_EXIT_CODE=0 \
	npx --prefix evals promptfoo eval \
	  -c evals/clusterer_merge/promptfooconfig.yaml \
	  --tests $(TESTS_OUT) \
	  -o $(RESULTS_OUT) \
	  --repeat $(REPEAT) \
	  --max-concurrency $(MAXCONCURRENCY) \
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

eval-merge-smoke: eval-venv-sync
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval RESULTS_OUT := evals/results/clusterer-merge-smoke-$(TIMESTAMP).json)
	PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	PROMPTFOO_FAILED_TEST_EXIT_CODE=0 \
	npx --prefix evals promptfoo eval \
	  -c evals/clusterer_merge/smoke.promptfooconfig.yaml \
	  -o $(RESULTS_OUT) \
	  --max-concurrency $(MAXCONCURRENCY)
	$(PROMPTFOO_PYTHON) evals/clusterer_merge/report.py $(RESULTS_OUT)

eval-merge-sheet:
	$(eval _LIMIT_ARG := $(if $(LIMIT),--limit $(LIMIT),))
	$(eval _SEED_ARG  := $(if $(SEED),--seed $(SEED),))
	$(UV_PYTHON) evals/clusterer_merge/label_sheet.py export \
	  --dataset $(DATASET) --labels $(LABELS) --out $(SHEET) \
	  --only-unlabeled $(_LIMIT_ARG) $(_SEED_ARG)

eval-merge-import:
	$(UV_PYTHON) evals/clusterer_merge/label_sheet.py import \
	  --sheet $(SHEET) --labels $(LABELS)

eval-view:
	cd evals && npx promptfoo view

eval-rank-export:
	.claude/skills/prod-db/prod-db.sh psql -qAt -f - \
	  < evals/summarize_rank/export.sql \
	  | $(PROMPTFOO_PYTHON) evals/summarize_rank/build_dataset.py \
	      --out evals/summarize_rank/datasets/articles-$$(date -u +%F).jsonl

eval-rank: eval-venv-sync
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval TESTS_OUT := evals/results/summarize-rank-tests.yaml)
	$(eval RESULTS_OUT := evals/results/summarize-rank-$(TIMESTAMP).json)
	$(eval _NOCACHE_ARG  := $(if $(NOCACHE),--no-cache,))
	$(eval _EVAL_NO_CACHE := $(if $(NOCACHE),EVAL_NO_CACHE=1,))
	$(PROMPTFOO_PYTHON) evals/summarize_rank/render_tests.py \
	  --dataset $(RANK_DATASET) --out $(TESTS_OUT)
	$(_EVAL_NO_CACHE) PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	PROMPTFOO_FAILED_TEST_EXIT_CODE=0 \
	npx --prefix evals promptfoo eval \
	  -c evals/summarize_rank/promptfooconfig.yaml \
	  --tests $(TESTS_OUT) \
	  -o $(RESULTS_OUT) \
	  --repeat $(REPEAT) \
	  --max-concurrency $(MAXCONCURRENCY) \
	  $(_NOCACHE_ARG)
	$(PROMPTFOO_PYTHON) evals/summarize_rank/report.py $(RESULTS_OUT)

eval-rank-report:
	$(eval _LATEST := $(shell ls -t evals/results/summarize-rank-*.json 2>/dev/null | head -1))
	$(PROMPTFOO_PYTHON) evals/summarize_rank/report.py $(_LATEST)

eval-rank-smoke: eval-venv-sync
	$(eval TIMESTAMP := $(shell date -u +%Y%m%dT%H%M%SZ))
	$(eval RESULTS_OUT := evals/results/summarize-rank-smoke-$(TIMESTAMP).json)
	PROMPTFOO_PYTHON="$(PROMPTFOO_PYTHON)" \
	PROMPTFOO_FAILED_TEST_EXIT_CODE=0 \
	npx --prefix evals promptfoo eval \
	  -c evals/summarize_rank/smoke.promptfooconfig.yaml \
	  -o $(RESULTS_OUT) \
	  --max-concurrency $(MAXCONCURRENCY)
	$(PROMPTFOO_PYTHON) evals/summarize_rank/report.py $(RESULTS_OUT)
