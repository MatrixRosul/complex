# =============================================================================
# Complex — команди. `make` без аргументів покаже список.
#
# Локальна розробка йде БЕЗ докера: PostgreSQL і Redis уже стоять на машині,
# бекенд крутиться в uv-оточенні, фронт — у node. Docker тут потрібен рівно для
# двох речей: прод і перевірка інфри.
# =============================================================================

.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND  := backend
FRONTEND := frontend

# uv сам знайде інтерпретатор за requires-python (>=3.12,<3.13).
# Системний python3 (3.9) НЕ використовується ніде.
UV := uv

# Прод-стек: два env-файли — звичайний і той, що пінить образи по дайджесту.
COMPOSE := docker compose --env-file .env --env-file infra/images.env -f infra/docker-compose.yml

.PHONY: help
help:
	@echo ""
	@echo "  РОЗРОБКА"
	@echo "    make setup        встановити залежності (uv sync + npm ci)"
	@echo "    make dev          Django на :8000  (фронт — окремо: make dev-front)"
	@echo "    make dev-front    Next.js на :3000"
	@echo "    make migrate      застосувати міграції"
	@echo "    make migrations   згенерувати міграції"
	@echo "    make seed         демо-дані (seed_demo)"
	@echo "    make superuser    створити адміна"
	@echo "    make shell        Django shell"
	@echo ""
	@echo "  ЯКІСТЬ"
	@echo "    make lint         ruff + mypy (+ eslint/tsc, якщо є фронт)"
	@echo "    make fmt          ruff format"
	@echo "    make test         pytest"
	@echo ""
	@echo "  ДАНІ"
	@echo "    make sync         синхронізація прайсу з Google Sheets"
	@echo "    make feed         згенерувати фід Hotline"
	@echo ""
	@echo "  ПРОД (на VPS)"
	@echo "    make secrets-init створити infra/secrets/*"
	@echo "    make pin-images   перепінити базові образи по sha256"
	@echo "    make prod-config  перевірити compose + Caddyfile, нічого не піднімаючи"
	@echo "    make prod-up      підняти стек"
	@echo "    make prod-migrate міграції під lock_timeout"
	@echo "    make prod-logs    логи (S=web — конкретний сервіс)"
	@echo "    make prod-ps      стан і healthcheck'и"
	@echo "    make backup-now   позачерговий повний бекап"
	@echo "    make restore-drill  перевірити, що бекап РЕАЛЬНО відновлюється"
	@echo ""

# ---------------------------------------------------------------------------
# Розробка
# ---------------------------------------------------------------------------
.PHONY: setup
setup:
	cd $(BACKEND) && $(UV) sync
	@if [ -f $(BACKEND)/.env ]; then \
		echo "  = backend/.env вже є"; \
	else \
		cp $(BACKEND)/.env.example $(BACKEND)/.env && echo "  + backend/.env створено з прикладу"; \
	fi
	@if [ -f $(FRONTEND)/package.json ]; then \
		cd $(FRONTEND) && npm ci; \
	else \
		echo "  ~ frontend/ ще немає — пропускаю npm ci"; \
	fi
	@echo ""
	@echo "Далі:  make migrate && make seed && make dev"

.PHONY: dev
dev:
	cd $(BACKEND) && $(UV) run python manage.py runserver 0.0.0.0:8000

.PHONY: dev-front
dev-front:
	@test -f $(FRONTEND)/package.json || { echo "frontend/ ще не створено (трек FE у ROADMAP)"; exit 1; }
	cd $(FRONTEND) && npm run dev

# Воркер і beat локально — коли треба погасати задачі по-справжньому.
# (Швидкий шлях: CELERY_TASK_ALWAYS_EAGER=True у backend/.env — тоді вони не потрібні.)
.PHONY: worker
worker:
	cd $(BACKEND) && $(UV) run celery -A config worker -Q default,images,sync,ai -c 2 --loglevel=INFO

.PHONY: beat
beat:
	cd $(BACKEND) && $(UV) run celery -A config beat --scheduler=django_celery_beat.schedulers:DatabaseScheduler --loglevel=INFO

.PHONY: migrate
migrate:
	cd $(BACKEND) && $(UV) run python manage.py migrate

.PHONY: migrations
migrations:
	cd $(BACKEND) && $(UV) run python manage.py makemigrations

.PHONY: seed
seed:
	cd $(BACKEND) && $(UV) run python manage.py seed_demo

.PHONY: superuser
superuser:
	cd $(BACKEND) && $(UV) run python manage.py createsuperuser

.PHONY: shell
shell:
	cd $(BACKEND) && $(UV) run python manage.py shell

# ---------------------------------------------------------------------------
# Якість
# ---------------------------------------------------------------------------
.PHONY: lint
lint:
	cd $(BACKEND) && $(UV) run ruff check .
	cd $(BACKEND) && $(UV) run ruff format --check .
	cd $(BACKEND) && $(UV) run mypy .
	@if [ -f $(FRONTEND)/package.json ]; then \
		cd $(FRONTEND) && npm run lint && npx tsc --noEmit; \
	fi

.PHONY: fmt
fmt:
	cd $(BACKEND) && $(UV) run ruff format .
	cd $(BACKEND) && $(UV) run ruff check --fix .

.PHONY: test
test:
	cd $(BACKEND) && $(UV) run pytest

# ---------------------------------------------------------------------------
# Дані
#
# ⚠️ Обидві команди з'являються не одразу: sync_prices — трек SYNC, а
#    generate_hotline_feed — трек FEED (див. docs/ROADMAP.md). Поки їх немає,
#    manage.py чесно скаже «Unknown command» — це не поламаний Makefile.
# ---------------------------------------------------------------------------
.PHONY: sync
sync:
	cd $(BACKEND) && $(UV) run python manage.py sync_prices

.PHONY: feed
feed:
	cd $(BACKEND) && $(UV) run python manage.py generate_hotline_feed

# ---------------------------------------------------------------------------
# Прод
# ---------------------------------------------------------------------------
.PHONY: secrets-init
secrets-init:
	./infra/scripts/secrets-init.sh

.PHONY: pin-images
pin-images:
	./infra/scripts/pin-images.sh

# Перевірити конфіг, НЕ піднімаючи нічого. Робити перед кожним prod-up.
.PHONY: prod-config
prod-config:
	$(COMPOSE) config --quiet && echo "compose OK"
	$(COMPOSE) run --rm --no-deps --entrypoint caddy caddy validate --config /etc/caddy/Caddyfile

.PHONY: prod-up
prod-up:
	$(COMPOSE) up -d --remove-orphans

.PHONY: prod-down
prod-down:
	$(COMPOSE) down

.PHONY: prod-migrate
prod-migrate:
	$(COMPOSE) run --rm -e PGOPTIONS="-c lock_timeout=5s" web python manage.py migrate --noinput

.PHONY: prod-ps
prod-ps:
	$(COMPOSE) ps

# make prod-logs S=worker-sync
.PHONY: prod-logs
prod-logs:
	$(COMPOSE) logs -f --tail=200 $(S)

.PHONY: backup-now
backup-now:
	$(COMPOSE) exec -u postgres backup /usr/local/bin/backup.sh full

# Неперевірений бекап = відсутній бекап. Ганяється автоматом щопонеділка,
# але перед ризикованою міграцією варто запустити руками.
.PHONY: restore-drill
restore-drill:
	$(COMPOSE) exec -u postgres backup /usr/local/bin/restore-drill.sh
