# Benchmark-results dashboard (read-only)

A local, offline dashboard for the **completed CSE499B live benchmark**.

It shows the results of

```
results/phase2_modular_100run_2026-10-22/
```

— a reduced live run of **100 requests** on the modular task family, comparing
**Full-File Rewrite**, **Search/Replace** and **ITMS AST-Guided Surgical
Editing**.

The dashboard is **read-only**. It never runs a benchmark, never contacts a
model provider, never makes a network request of its own and never writes to
the run directory. Every number it displays is derived from the artefacts the
run already produced and is cross-checked against the offline analysis file.

---

## 1. Start it

From the project root:

```bash
cd /Users/noshinebnatadrita/Documents/499B/dashboard/benchmark-results && \
  ../../.venv/bin/python -m streamlit run app.py
```

Then open:

```
http://localhost:8501
```

> The `cd` matters: Streamlit reads `.streamlit/config.toml` from the
> **current working directory**, and that file is what binds the server to
> `localhost` only and switches usage statistics off.

If port 8501 is already taken, use another one:

```bash
cd /Users/noshinebnatadrita/Documents/499B/dashboard/benchmark-results && \
  ../../.venv/bin/python -m streamlit run app.py --server.port 8502
```

## 2. Stop it

Press **Ctrl + C** in the terminal that is running the server.

If it was started in the background, kill it:

```bash
pkill -f "streamlit run app.py"
```

Nothing is left behind: the dashboard keeps no state and writes no files.

---

## 3. What you will see

| Tab | Content |
|---|---|
| **Overview** | Summary cards, the three-method comparison table, and interactive charts: official-evaluator pass rate, token usage (input + output stacked), mean/median latency, and the prompt-size difference. |
| **Method comparison** | Per-method detail (scheduled, evaluated, passes, failures, syntax validity, retries), the efficiency ranking, the per-task table and an interactive per-task pass-rate heatmap. |
| **Attempts** | The attempt-level table with filters for **method**, **task id**, **repetition** and **evaluation status**, plus a free-text search, and a CSV download of the filtered rows. |
| **Figures** | The 12 existing figures from `analysis/figures/`, shown unchanged. |
| **Reports** | The run's Markdown reports (`PHASE2_REDUCED_100_REPORT.md`, `analysis/RESULTS_SUMMARY.md`), readable in the page and downloadable. |
| **Integrity** | The consistency check between the dashboard and `analysis/analysis.json`, the artefact cross-checks, and the schedule that was actually used. |

### The numbers

* **Requested attempts** — provider requests issued (from the post-run budget state).
* **Officially evaluated** — attempts that reached the official Phase 0 evaluator.
* **Evaluator passes / failures** — the official evaluator's verdicts.
* **Never evaluated** — attempts that never reached the evaluator.
* Pass rates always use **evaluated attempts** as the denominator; the
  completion rate uses the **scheduled** cells, and each table says which.

---

## 4. Limitations shown in the dashboard

The dashboard opens with an expanded panel that discloses:

1. **Reduced request budget** — at most 100 live requests in total. This is
   *not* 20 tasks × 5 repetitions × 3 methods (that design needs 300
   requests). The budget was split **34 / 33 / 33** across the methods.
2. **Unequal repetition counts** — of the 60 (task, method) cells, 60 carry
   one attempt and 40 carry a second one; every task receives the same total
   number of attempts, so the imbalance is *within* a task.
3. **The prompt-length difference is a confound** — the ITMS adapter builds a
   pruned skeleton, so it is shown a much smaller prompt than the other two
   methods. Part of its total-token advantage therefore comes from the input
   side, not from a cheaper edit. The output-token column is the fairer
   comparison.
4. **Authored dataset, small corpus** — the task set was written for this
   project; it is clean, it satisfies the pre-registered representative-task
   rule, and it is not a real repository. These are not HumanEval tasks.
5. **Cost data is unavailable** — the provider reported no cost for any
   attempt, so the monetary cost is *unavailable*; it is never shown as zero.
6. **Seed honouring is unverified** — no repeated-request probe was run.
7. **Correctness is at a ceiling** — every evaluated attempt passed, so this
   run compares efficiency, not accuracy.

---

## 5. Files

```
dashboard/benchmark-results/
├── app.py                        the Streamlit dashboard (presentation only)
├── dashboard_data.py             the data layer: pure reader, no Streamlit import
├── README.md                     this file
├── .streamlit/config.toml        loopback binding, usage statistics off, theme
└── tests/test_dashboard_data.py  offline tests
```

The data layer is deliberately free of any `streamlit` import, so it can be
tested without a Streamlit runtime; the presentation layer is `app.py`.

## 6. What it reads (all read-only)

```
results/phase2_modular_100run_2026-10-22/attempts.jsonl
results/phase2_modular_100run_2026-10-22/attempts.csv
results/phase2_modular_100run_2026-10-22/summary.json
results/phase2_modular_100run_2026-10-22/run_metadata.json
results/phase2_modular_100run_2026-10-22/analysis/analysis.json
results/phase2_modular_100run_2026-10-22/analysis/figures/fig*.png
results/phase2_modular_100run_2026-10-22/analysis/RESULTS_SUMMARY.md
results/phase2_modular_100run_2026-10-22/PHASE2_REDUCED_100_REPORT.md
results/phase2_modular_100run_2026-10-22/artifacts/<attempt_id>/prompt.txt
```

The attempt records are read through the project's own read-only viewer
(`completed_benchmark_viewer.load_run`), so the dashboard reads exactly the
files the offline analysis read and inherits its JSONL/CSV cross-checks.

**No credential is ever displayed.** The run artefacts contain no API key, and
a test asserts that nothing credential-shaped reaches the page.

---

## 7. Verification

```bash
cd /Users/noshinebnatadrita/Documents/499B
.venv/bin/python -m pytest dashboard/benchmark-results/tests -q
```

The test file checks the artefact load, the agreement with
`analysis/analysis.json` (22 checked values, no disagreement), the derived
token split and prompt sizes, the disclosure of every limitation, the absence
of credentials, that the run directory is not modified, and that the
Streamlit app starts and renders **without an exception**.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` | Use `--server.port 8502` (or any free port). |
| The page shows an empty tab | Check the terminal for a Python traceback; the **Integrity** tab lists the artefact problems. |
| The server is reachable from the network | You did not `cd` into this directory, so `.streamlit/config.toml` was not picked up. |
| "Address already in use" on 8501 after a crash | `pkill -f "streamlit run app.py"` |
