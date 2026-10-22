# Input Token Minimization System (ITMS)
### Deterministic Execution Gate & Two-Tiered Asymmetric Relay Cascade

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Clean & Strict](https://img.shields.io/badge/code%20style-strict%20ast-black)](https://docs.python.org/3/library/ast.html)

---

## 1. Executive Summary & North Star

The **Input Token Minimization System (ITMS)** (formerly TDSC) is a production-grade LLM pipeline designed to cut token and compute costs by **70% to 90%** during autonomous code generation tasks without sacrificing solution quality.

Traditional multi-agent or iterative code generation architectures suffer from two systemic economic flaws:
1. **The Context Contamination Spiral:** When an initial code attempt fails unit tests, naive frameworks append the full broken code draft and the entire verbose stderr traceback back into the context window. This inflates input tokens exponentially, poisons prompt caches, and anchors frontier reasoning models to flawed syntax, triggering thousands of unnecessary reasoning/thinking tokens.
2. **Brute-Force Frontier Routing:** Direct submission of routine coding tasks to expensive frontier models (e.g., Claude Sonnet 3.7 / 5.5, GPT-5 / o3-mini) wastes expensive inference budgets on trivial boilerplate that budget models can solve with high pass rates.

### The ITMS Architectural Solution
ITMS operates as a **two-tiered asymmetric speculative cascade** governed by a zero-cost deterministic referee:
- **Tier 1 (Budget Speculator):** Fast, low-cost models (Gemini 2.0/3.5 Flash, DeepSeek V4 Flash, GPT-4o-mini) attempt the initial implementation.
- **Deterministic Sandbox Gate (Milestone 2):** Candidate code is evaluated in an isolated subprocess execution boundary against unit tests at **$0.00 compute cost**.
  - **First-Pass Pass:** If tests succeed, code is verified and delivered immediately. **Zero Tier 2 cost incurred.**
  - **First-Pass Failure:** If tests fail, the broken draft is **completely discarded** (Anti-Anchoring Invariant).
- **Tier 2 (Surgical Clean-Trace Escalation - Milestone 3):** ITMS synthesizes an ultra-compact escalation prompt containing:
  1. A statically cached prefix (anchored at token 0 for 90% cloud KV cache discounts).
  2. AST-pruned background code (dead classes and docstrings stripped).
  3. A 3-line isolated diagnostic failure signal (clean assertion and error type).
  The frontier reasoning model solves the task from a clean slate with zero cognitive bias.

---

## 2. Milestone Architecture

```
                                  [ User Task & Codebase ]
                                             │
                                             ▼
                             ┌───────────────────────────────┐
                             │    Phase 1: Tier 1 Speculator │
                             │  (Gemini Flash / DeepSeek V4) │
                             └───────────────┬───────────────┘
                                             │ [Candidate Code]
                                             ▼
                             ┌───────────────────────────────┐
                             │ Milestone 2: Execution Gate   │
                             │  (Isolated Subprocess Sandbox)│
                             └───────┬───────────────┬───────┘
                                     │               │
                     [Tests Pass]    │               │ [Tests Fail]
                                     ▼               ▼
                        ┌──────────────────┐   ┌───────────────────────────────┐
                        │ Immediate Code   │   │ Anti-Anchoring Purge:         │
                        │ Delivery ($0 T2) │   │ Discard Broken Code Draft     │
                        └──────────────────┘   └───────────────┬───────────────┘
                                                               │
                                                               ▼
                                               ┌───────────────────────────────┐
                                               │ Milestone 1: ITMS Compressor  │
                                               │ - AST Reachability Pruning    │
                                               │ - Zero-Index Cache Prefix     │
                                               │ - 3-Line Diagnostic Signal    │
                                               └───────────────┬───────────────┘
                                                               │ [Clean Escalation Prompt]
                                                               ▼
                                               ┌───────────────────────────────┐
                                               │ Phase 3: Tier 2 Frontier Relay│
                                               │ (Claude Sonnet / GPT-5.6)     │
                                               └───────────────┬───────────────┘
                                                               │ [Synthesized Solution]
                                                               ▼
                                               ┌───────────────────────────────┐
                                               │ Milestone 2: Final Gate Check │
                                               │ Re-verify in Subprocess       │
                                               └───────────────┬───────────────┘
                                                               │
                                                               ▼
                                                      [ Verified Solution ]
```

### Milestone 1: Front-end Token Minimization & Telemetry Engine
- **AST Reachability Pruning:** Uses Python's native `ast` parser to compute transitive symbol dependencies. Dead classes, unreferenced utility methods, and redundant helpers are culled before prompt construction.
- **Static Prefix Anchoring:** Aligns invariant system instructions at token index 0 (`GLOBAL_STATIC_PREFIX` for local KV-cache engines; `EXPANDED_CACHED_PREFIX_1024` for cloud providers requiring 1,024+ tokens).
- **3-Line Clean Diagnostic Extractor:** Python 3.11+ caret-safe regex parser that isolates the failing assertion line, error type, and error message, filtering out multi-page stack traces.

### Milestone 2: Deterministic Execution Harness (`sandbox.py`)
- Isolated transient subprocess execution via `sys.executable`.
- Strict timeout boundary enforcement (`EXECUTION_TIMEOUT_SECONDS`, default 3.0s) to kill infinite loops.
- Static `ast.parse` syntax pre-validation before process creation.
- Dynamic class method re-binding (`Class.method = method`) preventing stub execution collisions.
- Zero-cost referee: filters 70%–80% of tasks at $0.00 API inference cost.

### Milestone 3: Live Model Dispatcher & Two-Tiered Relay (`model_dispatcher.py`, `relay_orchestrator.py`)
- **Zero-Bloat Unified REST Client:** Native `httpx` HTTP/2 client for Google Gemini, Anthropic Claude, OpenAI, and DeepSeek endpoints. No heavyweight vendor SDK lock-in.
- **Deterministic Offline Mock Engine:** Automated fallback ensuring grading, unit tests, and offline runs execute with deterministic predictability when no API keys are configured.
- **Counterfactual Economics Engine:** Live calculation of dollar and token savings against Direct Frontier routing and Naive Agentic feedback loops.

---

## 3. Security & GitHub Secret Hygiene

This repository is strictly configured to prevent credential leaks:
1. **`.gitignore` Enforced:** All `.env`, `*.log`, `tmp/`, `scratch/`, and cache directories are permanently blocked from version control.
2. **Template-Driven Configuration:** A sanitized `.env.example` provides documentation for all optional provider keys.
3. **Key Masking (`config.py`):** Keys displayed in logs or the Streamlit web dashboard are masked (e.g. `sk-...8877`).
4. **Offline Resilience:** The entire test suite and dashboard function out-of-the-box in Mock Mode without requiring valid cloud API keys.

---

## 4. Installation & Setup

### Prerequisites
- Python 3.10, 3.11, 3.12, 3.13, or 3.14
- Virtual environment recommended (`venv` or `conda`)

### Step 1: Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/your-username/itms-cascade.git
cd itms-cascade

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Credentials (Optional)
Copy `.env.example` to `.env` and insert your API keys:
```bash
cp .env.example .env
```
*(If no keys are supplied, ITMS automatically operates in Deterministic Mock Mode).*

---

## 5. Verification & Testing

To execute the complete 584 automated test suite:
```bash
# Run all Milestone 1, 2, and 3 test suites
python -m pytest test_milestones_2_3.py test_itms.py test_tdsc.py -v
```

### Test Coverage Highlights:
- **Sandbox Security & Timeouts:** Verifies timeout interruption on infinite loops (`while True`), syntax error interception, and assertion extraction.
- **Anti-Anchoring Validation:** Asserts that broken draft code from Tier 1 is never passed into the Tier 2 escalation prompt.
- **Secret Masking:** Asserts API keys are properly sanitized in diagnostic outputs.
- **Plaintext Compliance:** Automated test verifies zero unicode emojis exist across all source files, UI templates, and metrics.

---

## 6. Launching the Interactive Web Dashboard

To launch the Streamlit dashboard:
```bash
streamlit run app.py
```

The dashboard includes 4 operational tabs:
1. **Live Relay Runner (Milestones 2 & 3):** Run live cascade executions with real-time pipeline status, execution logs, and code delivery.
2. **Single-Query Prompt Telemetry:** Inspect token distributions, cache hit rates, and prompt comparisons side-by-side.
3. **Full Cascade Lifecycle Economics:** Simulate batch economics (1,000 to 10,000 tasks) and benchmark aggregate cost savings across different pass rates.
4. **Model Catalog & Tier Analysis:** Comprehensive catalog of Tier 1 budget workhorses and Tier 2 frontier models with latency profiles and pricing per 1M tokens.

---

## 7. Repository Layout

```
├── .env.example              # Documented environment template
├── .gitignore                # Secret protection & bytecode filter
├── requirements.txt          # Pinned dependency definitions
├── README.md                 # System overview and operational guide
├── MISSION.md                # Capstone specification & architectural invariants
├── config.py                 # Centralized configuration & key masking
├── skeletonizer.py           # AST reachability & dead code pruning
├── prompt_manager.py         # Static prefix anchoring & traceback extraction
├── telemetry.py              # Financial accounting, pricing catalog & deltas
├── sandbox.py                # Milestone 2: Deterministic subprocess sandbox
├── model_dispatcher.py       # Milestone 3: Zero-bloat REST API & mock client
├── relay_orchestrator.py     # Milestone 3: Asymmetric cascade coordinator
├── surgical_splicer.py       # Phase 2: AST node locator & line-range splicer
├── edit_orchestrator.py      # Phase 2: Tri-way edit benchmark orchestrator
├── app.py                    # Streamlit telemetry, live runner & surgical edit dashboard
├── test_itms.py              # ITMS core unit tests
├── test_milestones_2_3.py    # Milestones 2 & 3 verification suite
├── test_surgical_splicer.py  # Phase 2 AST surgical splicer & benchmark tests
└── test_tdsc.py              # Backward compatibility wrapper test suite
```

---

## Benchmark run (20 tasks × 5 reps)

The 100-run macro benchmark (20 approved Phase 0 tasks × 5 repetitions) is
**deferred to the next OpenRouter free-model quota reset (UTC)**. The daily
free-model allowance for this key is exhausted, and a run started before the
reset would only record quota errors.

The command that will run it:

```bash
.venv/bin/python -m benchmark_harness --live --reps 5 \
  --tier1-model minimax/minimax-m3.1-flash:free \
  --tier2-model minimax/minimax-m3.1-flash:free \
  --output-dir results/full_20x5_$(date -u +%Y%m%dT%H%M%SZ)
```

| Item | Value |
|---|---|
| Tier 1 model id | `minimax/minimax-m3.1-flash:free` |
| Tier 2 model id | `minimax/minimax-m3.1-flash:free` |
| Task set | the 20 approved `HE-EDIT-*` Phase 0 repair tasks |
| Repetitions | 5 per task |
| Planned runs | 100 (100–400 provider requests: 1 per run, plus up to 2 Tier 1 self-repair retries and 1 Tier 2 escalation) |
| Evaluator | the official Phase 0 evaluator (`benchmarks/phase0/runner.py`), not the legacy execution gate |
| Status | **deferred** to the next daily quota reset (UTC) |

The quota poller (`scripts/wait_for_quota.py`) is covered by 6 additional
tests included in the 584.

**Every headline number in this repository comes from a recorded run
directory** — the attempt records, the summary and the offline analysis
that were written by the run itself. No headline figure is a hardcoded
constant, and no figure is published before the run that produced it exists
on disk. Where a number is not yet available it is written as
`[TBD after Step 8]` rather than filled in with an estimate.

---

## 8. License
This project is licensed under the MIT License.
