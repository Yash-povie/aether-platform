.PHONY: up down build test migrate logs shell-api keygen lint helm-lint helm-dry-run

# ─── Local Development ───────────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

down-volumes:
	docker compose down -v

build:
	docker compose build

build-no-cache:
	docker compose build --no-cache

logs:
	docker compose logs -f api-gateway agent-engine

logs-all:
	docker compose logs -f

# ─── Testing ─────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

test-cov:
	pytest tests/ -v --cov=services --cov=shared --cov-report=html
	@echo "Coverage report written to htmlcov/index.html"

# ─── Database ────────────────────────────────────────────────────────────────
migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

migrate-new:
	@read -p "Migration message: " msg; alembic revision --autogenerate -m "$$msg"

# ─── Shells ──────────────────────────────────────────────────────────────────
shell-api:
	docker compose exec api-gateway bash

shell-db:
	docker compose exec postgres psql -U aether -d aether

# ─── Key Generation ──────────────────────────────────────────────────────────
keygen:
	openssl genrsa -out private.pem 2048
	openssl rsa -in private.pem -pubout -out public.pem
	@echo ""
	@echo "Keys generated."
	@echo "Set in .env:"
	@echo "  JWT_PRIVATE_KEY=$$(cat private.pem | base64 -w 0)"
	@echo "  JWT_PUBLIC_KEY=$$(cat public.pem | base64 -w 0)"
	@echo ""
	@echo "IMPORTANT: Move private.pem and public.pem to a secure location or delete them."

# ─── Linting ─────────────────────────────────────────────────────────────────
lint:
	ruff check services/ shared/

lint-fix:
	ruff check --fix services/ shared/

# ─── Helm ────────────────────────────────────────────────────────────────────
helm-lint:
	helm lint infra/helm/aether

helm-dry-run:
	helm upgrade --install aether infra/helm/aether \
		--namespace aether-production \
		--create-namespace \
		--dry-run \
		--debug

helm-template:
	helm template aether infra/helm/aether --namespace aether-production

# ─── Secrets (for local use only) ────────────────────────────────────────────
b64-encode:
	@read -p "Value to encode: " val; echo -n "$$val" | base64