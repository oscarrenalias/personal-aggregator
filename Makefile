VERSION := $(shell git describe --tags --always --dirty)

.PHONY: build up down logs version openapi

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
