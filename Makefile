# Shortcuts. Each one is a plain command you can also type.

.PHONY: test eval eval-production eval-quick eval-search eval-instructions eval-judge eval-plan promote rollback export

test:            ## every test: Python, then the PWA
	uv run pytest -q
	cd web && npm test && npm run check

eval:            ## the candidate (prompts labelled staging) on every set: sheets, tests, search, instructions, judge, plan
	uv run skoolathome eval run --label staging

eval-production: ## the production configuration: the baseline `make eval` compares to
	uv run skoolathome eval run --label production

eval-quick:      ## test detection only, and the traps that need no model: a minute, not a night
	uv run skoolathome eval run --label staging --family tests
	uv run skoolathome eval run --label staging --family sheets --case trap-empty-lesson --case trap-too-short

eval-search:     ## recall@3 of the search (T31): minutes, the embedding model only
	uv run skoolathome eval run --label staging --family retrieval

eval-instructions: ## the parent's sentences understood field by field (T31)
	uv run skoolathome eval run --label staging --family instructions

eval-judge:      ## the judge against the parent's annotations (T33)
	uv run skoolathome eval run --label staging --family judge

eval-plan:       ## the evening plan's tests (T19, T31): seconds, no model
	uv run skoolathome eval run --label staging --family plan

promote:         ## the staging prompts go to production, only those whose `make eval` passed (journal: T32)
	uv run skoolathome eval promote --why "$(WHY)"

rollback:        ## production back on the previous version: make rollback PROMPT=sheet-generator
	uv run skoolathome prompt rollback $(PROMPT) --why "$(WHY)"

export:          ## the sets, every prompt version and the journal in data/evals/ (the evening does it on Fridays)
	uv run skoolathome eval export
