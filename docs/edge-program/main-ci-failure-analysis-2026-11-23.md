# Main-branch CI failure classification

**Audit window:** the 12 non-successful `push` workflow runs returned by GitHub's `branch=main&per_page=100` Actions API snapshot on 2026-11-23. The most recent main run at the time of capture, run `45048365531` on `3c99b5dddc775db888822b4cec1849e5ebca56c8`, completed successfully on Ubuntu, Windows, and both reproducibility jobs.

| Main run(s) | Job evidence | Root-cause classification |
|---|---|---|
| `42780156564`, `42786408945`, `42794170512` | On both OS jobs, setup, checkout, Python setup and install succeeded; `Lint` failed. Logs show Ruff `F401` unused-import diagnostics. Test and downstream steps were skipped. | **Code-quality failures**, not runner/billing failures and not test-suite failures. |
| `40013707411`, `40014754004`, `40813818332` | On both OS jobs, setup, lint, type check and migrations succeeded; `Test suite` failed. Downloaded logs show DuckDB `BinderException` for deprecated lambda-arrow SQL in technical-indicator and committee tests. | **Application/test compatibility failures** caused by SQL syntax rejected by the installed DuckDB version. |
| `38854824098` | Setup, lint, type check and migrations succeeded; `Test suite` failed on both OSes. Logs show a robots wildcard expectation mismatch and a network-failure path that did not raise the expected `FetchError`. | **Application/test behavior failures**; available logs do not indicate hosted-runner or billing failure. |
| `40070029654`, `40070163997`, `40102159941`, `43910984693`, `43914438801` | GitHub reports both OS jobs as completed with `conclusion=failure`, but returns no step summaries and no retrievable job logs (`logs_url` absent or log artifact returns `BlobNotFound`). | **Undetermined.** The API evidence is insufficient to decide whether setup, tests, or another step failed. They are not labeled as billing/runner incidents without proof. |

## Findings

- Among the seven runs with usable step/log evidence, each failure is a repository lint or test failure after runner setup completed successfully. No runner provisioning, billing, or cancellation failure was observed in that evidence.
- Five older failures have no retained step/log evidence. Their failure origin remains unknown; their “test” job name alone is not proof that pytest ran.
- The current `main` run `45048365531` is green across Ubuntu and Windows test jobs and Ubuntu/Windows reproducibility jobs. This is evidence for that captured main SHA only, not for other PR heads.

## Sources

- GitHub Actions query: `https://api.github.com/repos/crakashp2905-hub/Financial-Brain/actions/runs?branch=main&per_page=100`
- Run IDs listed above are linked directly by the GitHub API at `https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/<RUN_ID>`; job logs were fetched for runs `42780156564`, `42786408945`, `42794170512`, `40813818332`, `40014754004`, `40013707411`, and `38854824098`.
- Latest green main run: `https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/45048365531`.

**Checklist result: PARTIAL.** The available root causes are separated from infrastructure failures, but the five failures without retained logs cannot be conclusively classified. No cause has been invented for them.
