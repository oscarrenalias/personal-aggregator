VERSION := $(shell git describe --tags --always --dirty)

.PHONY: build up down logs version

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
