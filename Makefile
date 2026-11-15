# Shortcuts. Each one is a plain command you can also type.

.PHONY: test eval eval-production eval-quick promote

test:            ## every test: Python, then the PWA
	uv run pytest -q
	cd web && npm test && npm run check

eval:            ## the candidate (prompts labelled staging) on the reference set, against production (T23)
	uv run skoolathome eval run --label staging

eval-production: ## the production configuration: the baseline `make eval` compares to
	uv run skoolathome eval run --label production

eval-quick:      ## test detection only, and the traps that need no model: a minute, not a night
	uv run skoolathome eval run --label staging --family tests
	uv run skoolathome eval run --label staging --family sheets --case trap-empty-lesson --case trap-too-short

promote:         ## the staging prompts go to production, only those whose `make eval` passed
	uv run skoolathome eval promote
