.PHONY: install run run-https up down smoke

install:
	pip install -r requirements.txt

run:
	python app.py

run-https:
	python run_https.py

up:
	docker compose up --build

down:
	docker compose down

smoke:
	python scripts/smoke_test.py
