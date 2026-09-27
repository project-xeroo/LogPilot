# LogPilot AI Agent - common tasks.  `make help`
.DEFAULT_GOAL := help
PY ?= python
COMPOSE ?= docker compose

.PHONY: help up down logs build ps sample-logs e2e test test-py test-web openapi k8s-manifests console-dev clean

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

up:  ## build and start the full stack (console http://localhost:8080, API http://localhost:8000/docs)
	$(COMPOSE) up -d --build

down:  ## stop the stack (keeps data volumes)
	$(COMPOSE) down

logs:  ## follow logs of all services
	$(COMPOSE) logs -f --tail=100

build:  ## build all images
	$(COMPOSE) build

ps:  ## show service status
	$(COMPOSE) ps

sample-logs:  ## generate a realistic multi-format demo dataset (two past outages + a live build-up)
	$(PY) scripts/generate_sample_logs.py --out data/sample-logs

e2e:  ## end-to-end smoke test against a running stack
	$(PY) scripts/e2e_smoke.py --url http://localhost:8000 --zip data/sample-logs/logpilot-demo-logs.zip

test: test-py test-web  ## all unit tests

test-py:  ## backend unit tests (run from each service dir; needs `pip install -e shared` deps)
	@for s in log-ingestion-service processing-worker forecasting-service ai-service api-gateway; do \
	  echo "== $$s"; (cd services/$$s && PYTHONPATH=../.. $(PY) -m pytest tests -q) || exit 1; done

test-web:  ## frontend unit tests
	cd frontend && npm test

openapi:  ## regenerate docs/api/openapi-spec.yml from the gateway app
	cd services/api-gateway && PYTHONPATH=../.. $(PY) -c "import yaml; from app.main import app; open('../../docs/api/openapi-spec.yml','w').write('# Generated: make openapi\n'+yaml.safe_dump(app.openapi(), sort_keys=False, width=110))"

k8s-manifests:  ## regenerate infra/k8s/base workloads from infra/k8s/gen-manifests.js
	node infra/k8s/gen-manifests.js

console-dev:  ## run the console with hot reload against a local API
	cd frontend && npm install && npm run dev

clean:  ## remove containers, volumes and build output
	$(COMPOSE) down -v
	rm -rf frontend/dist
