# Changing the agent: the procedure

Every change to the agent (a prompt, a model, the engine, a quantization) follows the same loop. It starts from a problem you have seen, it is measured before, watched after, written in the journal, and can be undone with one command.

## The loop

1. **The weekly review shows a problem.** An indicator is past its threshold, a report says "wrong answer" or "not covered", or the child asks « Je n'ai pas compris » about the same concept again.
2. **Open the traces concerned.** Use the saved views in Langfuse (`skoolathome scores views` lists them). Filter by rule, prompt version or report reason.
3. **Add the cases to the reference set.**
   - `skoolathome eval add-report <id> --answer "…"` turns a report into a case.
   - `skoolathome eval add-sheet <id>` turns a good sheet into a case.
   - `skoolathome eval add-retrieval <lesson> --document "…" --page N` adds a lesson and its expected page. Decide the page by reading the lesson, never from what the search returns.
   - `skoolathome eval add-corrections` adds every [Modifier] from the parent view.
   - The sheets you annotate in the weekly review become judge cases by themselves.
4. **Change the prompt with the label `staging`.** Edit `src/skoolathome/agent/prompts/<name>.md`, then run `skoolathome prompt push <name> --label staging`. A model or engine change is a change to `models.toml`, tried with `SKOOLATHOME_MODELS=…`.
5. **Run `make eval`.** It runs every set:

   | Set | What it checks | Blocking threshold |
   |---|---|---|
   | sheets | the agent's sheets | 100 % on correct answers, grounding and traps |
   | tests | test detection | 100 % |
   | search | recall@3 | 90 % |
   | instructions | the parent's sentences, field by field | 90 % |
   | judge | the judge's agreement with your annotations | 80 % |
   | plan | the evening plan's tests | 100 % |

   One summary table at the end says what blocks. For a quick look while you work, use `make eval-search`, `make eval-instructions`, `make eval-judge`, `make eval-plan`, or `make eval-quick`.
6. **Promote.** Run `skoolathome prompt promote <name> <version> --why "<the problem>"`, or `make promote WHY="…"` for every staging prompt that passed. A version without a passing `make eval` of every set that uses it is refused. The journal (`skoolathome changes`) records the date, the reason, the runs behind the change, the versions before and after, and the command that undoes it. A change to `models.toml` is recorded by the next evening.
7. **Watch for two weeks.**
   - The weekly review compares the real feedback before and after each change: success alone, reports, wrong answers.
   - A wrong answer reported on a version promoted less than two weeks ago sends a notification at once, with the rollback command.
8. **Roll back if needed.** Run `skoolathome prompt rollback <name>`, or `make rollback PROMPT=<name> WHY="…"`. For a model, an engine or a quantization, roll back with a `git revert` of the `models.toml` commit. Either way, it goes in the journal.

## Keep some cases for validation only

With one pupil, the reference set stays small. A prompt tuned again and again can end up "learning" the set. So mark a few cases as validation only:

```bash
uv run skoolathome eval hold <id>            # --release to use it for tuning again
uv run skoolathome eval run --tuning …       # while you tune: without those cases
```

`make eval`, the run that decides a promotion, sees every case, including the held ones.

## Backup

Every Friday, the evening exports the sets, every version of every prompt, and the journal to `data/evals/<date>/`. You can also run `make export` by hand. `skoolathome eval restore data/evals/<date>` rebuilds the sets and the prompts in an empty Langfuse.

`data/evals/` holds your child's data. It stays out of the repository, but it belongs in your usual backup.
