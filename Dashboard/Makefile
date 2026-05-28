.PHONY: help build up down shell migrate superuser load-dummy ingest-belauri test lint

help:
	@echo "Nepal Air Quality Dashboard — make targets"
	@echo "  build           Build Docker images"
	@echo "  up              Start all services"
	@echo "  down            Stop all services"
	@echo "  shell           Django shell inside web container"
	@echo "  migrate         Run migrations"
	@echo "  superuser       Create Django superuser"
	@echo "  load-dummy      Load dummy data (2 sites, 3 sensors, 30d readings)"
	@echo "  ingest-belauri  Ingest actual Belauri CSV files"
	@echo "  test            Run test suite"
	@echo "  lint            Run flake8"

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

shell:
	docker compose exec web python manage.py shell

migrate:
	docker compose exec web python manage.py migrate

superuser:
	docker compose exec web python manage.py createsuperuser

load-dummy:
	docker compose exec web python manage.py load_dummy_data

ingest-belauri:
	docker compose exec web python manage.py run_ingestion --source belauri_csv

test:
	docker compose exec web python manage.py test apps

lint:
	docker compose exec web flake8 apps nepal_aq cms --max-line-length=120

# Local dev without Docker
local-setup:
	pip install -r requirements.txt
	python manage.py migrate
	python manage.py load_dummy_data

local-run:
	python manage.py runserver
