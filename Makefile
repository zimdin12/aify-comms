.PHONY: help setup build up down restart logs test dev clean mcp-install

help:
	@echo "Agentify Container Commands:"
	@echo "  make setup      - Copy config templates (.env, service.json)"
	@echo "  make build      - Build Docker image"
	@echo "  make up         - Start service with all sub-services"
	@echo "  make down       - Stop service"
	@echo "  make restart    - Rebuild and restart"
	@echo "  make logs       - Tail service logs"
	@echo "  make test       - Test all endpoints"
	@echo "  make dev        - Start with hot-reload (mounts source)"
	@echo "  make clean      - Remove containers and volumes"
	@echo "  make mcp-install - Install stdio MCP server dependencies"

setup:
	bash setup.sh

build:
	bash scripts/stamp.sh
	docker compose build

up:
	bash scripts/compose-up.sh -d --build

down:
	docker compose down

restart:
	bash scripts/stamp.sh
	docker compose up -d --build --force-recreate

logs:
	docker compose logs -f service

test:
	bash scripts/test-endpoints.sh

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
	@echo "Dev mode: http://localhost:8800 with hot-reload"

clean:
	docker compose down -v --remove-orphans

mcp-install:
	cd mcp/stdio && npm install
