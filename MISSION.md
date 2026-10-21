# Mission Statement: Input Token Minimization System (ITMS)
### Core Architecture: Test-Driven Speculative Cascade (TDSC)

---

## 1. Project Objective and North Star

Our mission is to build a cost-efficient, production-grade code generation and execution engine that balances accuracy, cost, and latency through a two-tiered asymmetric model relay.

We are modernizing and evolving the foundational principles of budget-constrained model cascading (originally introduced by FrugalGPT) to match the current realities of modern large language models, inference pricing structures, prompt-caching hardware, and test-time compute.

**North Star Goal:** Achieve parity with the accuracy of top-tier frontier reasoning models on software engineering tasks while reducing total token expenditure and API costs by **70% to 90%**.

---

## 2. The Core Problems We Are Solving

Conventional code-generation architectures fall into two economically unsustainable extremes:

1. **The Brute-Force Frontier Approach:** Sending every programming query directly to expensive frontier reasoning models (e.g., Claude Sonnet 3.7 / 5.5, GPT-5 / o3-mini), burning costly input and reasoning tokens on boilerplate tasks that small models can solve easily.
2. **The Naive Agentic Contamination Spiral:** Iteratively prompting models to review, critique, and debug their own broken code drafts. This causes **cognitive anchoring**, pollutes prompt cache lines, and traps the pipeline in expensive circular debugging loops that burn thousands of reasoning tokens.

---

## 3. High-Level Architectural Vision

The system operates as an asymmetric, test-driven relay between two model tiers, governed by local, deterministic execution feedback:

```
[ User Coding Task & Specification ]
                 │
                 ▼
┌────────────────────────────────────────────────────────┐
│ Phase 1: Tier 1 Fast Speculator (Gemini / DeepSeek)    │
└────────────────────────┬───────────────────────────────┘
                         │ [Candidate Code]
                         ▼
┌────────────────────────────────────────────────────────┐
│ Phase 2: Deterministic Verification Gate ($0 Compute)  │
│ (AST Syntax Pre-check -> Isolated Subprocess Sandbox)  │
└────────────────────────┬───────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
    [Tests Pass]                    [Tests Fail]
         │                               │
         ▼                               ▼
┌──────────────────┐            ┌────────────────────────────────────────┐
│ Immediate Code   │            │ Phase 3: Anti-Anchoring Purge          │
│ Delivery ($0 T2) │            │ (Discard Broken Code Draft Completely) │
└──────────────────┘            └────────────────┬───────────────────────┘
                                                 │
                                                 ▼
                                ┌────────────────────────────────────────┐
                                │ ITMS Context Compression Engine        │
                                │ - AST Reachability Pruning (Dead Code) │
                                │ - Token-0 Prefix Cache Alignment       │
                                │ - 3-Line Isolated Diagnostic Signal    │
                                └────────────────┬───────────────────────┘
                                                 │ [Clean Escalation Prompt]
                                                 ▼
                                ┌────────────────────────────────────────┐
                                │ Phase 4: Tier 2 Frontier Relay         │
                                │ (Claude Sonnet 5.5 / GPT-5.6 Astra)    │
                                └────────────────┬───────────────────────┘
                                                 │ [Synthesized Solution]
                                                 ▼
                                ┌────────────────────────────────────────┐
                                │ Phase 5: Final Subprocess Gate Check   │
                                └────────────────┬───────────────────────┘
                                                 │
                                                 ▼
                                        [ Verified Solution ]
```

* **The Fast First Pass (Tier 1):** Inexpensive, ultra-fast coding models (Gemini 3.5 Flash, DeepSeek V4 Flash, GPT-4o-mini) generate the initial implementation.
* **The Zero-Cost Referee (Deterministic Gate):** Instead of paying an LLM to evaluate code quality, a local subprocess sandbox checks syntax, compilation, and unit test execution in milliseconds at **$0.00 compute cost**.
* **Surgical Clean-Trace Escalation (Tier 2):** If and only if the code fails runtime verification, the system escalates to an advanced frontier reasoning model. To prevent cognitive bias and token inflation, the broken draft is discarded; Tier 2 receives only the original task specification, AST-pruned background context, and an isolated 3-line failure trace.

---

## 4. Guiding Engineering Principles & Architectural Invariants

Every module built in this system adheres to these foundational principles:

### A. Deterministic Verification Over Probabilistic Judges
Never use an LLM call or a statistical machine learning classifier to verify code if an operating system tool, compiler, AST parser, or test runner can verify it deterministically for free.

### B. The Anti-Anchoring Invariant
Do not forward flawed, broken code drafts to reasoning models. Research and empirical evidence show that reasoning models spend excessive tokens trying to reconcile, simulate, or patch bad drafts. Always escalate with a clean slate paired only with the precise runtime error and failed assertion.

### C. Hardware-Aware Token Economics & Cache Alignment
Token cost is dictated by GPU hardware mechanics:
* **Preserve the Static Prefix:** Keep the initial system prompts and instructions strictly identical across queries to maximize provider-level Prefix KV Cache reuse (unlocking 50% to 90% cache discounts).
* **Provider Cache Padding:** Support expanded 1,024+ token static system instructions to reliably trigger minimum cache thresholds on cloud providers (OpenAI, Anthropic).
* **AST Context Pruning:** Strip function bodies, comments, and docstrings from background helper files using AST skeletonization.
* **AST Symbol Reachability Analysis:** Analyze identifier references and cull completely unreferenced classes and functions to eliminate dead context weight.
* **Budget Output and Reasoning Tokens:** Prioritize controlling long reasoning traces, as thinking tokens dominate the compute bill.

### D. The Circuit-Breaker Policy (No Endless Retry Loops)
Unlike open-ended agentic frameworks that loop indefinitely while burning hundreds of dollars:
* Tier 1 gets exactly **1 speculative attempt**.
* Tier 2 gets exactly **1 surgical escalation attempt**.
* The maximum execution depth is strictly **2**. If both tiers fail, the task is flagged cleanly for human developer intervention.

### E. The Ground-Truth Contract (TDD Invariant)
ITMS operates under the **Test-Driven Development (TDD) Contract**:
* Ground-truth assertions originate from external specifications, benchmark suites (HumanEval, MBPP, SWE-bench), or human developer requirements.
* We explicitly reject naive self-referential test generation where Tier 1 writes its own tests, as autoregressive LLMs suffer from correlated failure modes (the model encodes its misunderstanding into both the test and the code).
* When symbolic assertions are required, prompts force symbolic expressions (`100 * (1 - 0.2)`) evaluated by Python's runtime rather than error-prone LLM mental arithmetic (`== 70`).

---

## 5. Comparative Positioning: ITMS vs. State-of-the-Art Literature

| Dimension | CodeT (NeurIPS 2022) | TestGen-LLM (Meta 2024) | ITMS / TDSC (Our Approach) |
| :--- | :--- | :--- | :--- |
| **Primary Goal** | Maximize pass@k accuracy via consensus | Augment test coverage in massive monolithic repos | **Cut token expenditure & API costs by 70%–90%** |
| **Compute Strategy** | **Token-Maximizing:** Generates $K \times M$ samples (20x–50x cost) | **Infrastructure-Heavy:** Requires mutation testing & coverage profilers | **Token-Minimizing:** Two-tiered asymmetric speculative cascade |
| **Test Verification** | Dual execution agreement matrix | Mutation score & line coverage | **Isolated subprocess deterministic execution gate ($0 compute)** |
| **Escalation Path** | High-density clustering | Pull-request test suite integration | **Surgical clean-trace escalation (Anti-Anchoring)** |
| **Cache Awareness** | None (Ignores hardware cache) | None | **Token-0 Prefix KV Cache alignment & AST pruning** |

---

## 6. Security Posture & GitHub Secret Isolation

To protect API credentials and maintain production distribution hygiene:
* **Permanent Version Control Exclusion:** `.gitignore` permanently excludes `.env`, `*.log`, `tmp/`, `scratch/`, and bytecode caches.
* **Documented Environment Template:** `.env.example` provides explicit variable definitions for `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `DEEPSEEK_API_KEY`.
* **Centralized Key Masking:** All API keys loaded via `config.py` are masked (e.g., `sk-...8877`) before appearing in logs or dashboard views.
* **Deterministic Offline Fallback:** When no credentials are provided, `model_dispatcher.py` falls back seamlessly to an offline deterministic mock generator, ensuring unit tests and CI/CD pipelines always pass.

---

## 7. Implementation Milestones Roadmap

### Milestone 1: Front-end Token Minimization & Telemetry Engine [COMPLETED]
- AST Reachability Pruning & Docstring Stripping (`skeletonizer.py`).
- Static Prefix Cache Anchoring (73-token and 1,024-token cloud threshold) (`prompt_manager.py`).
- 3-Line Caret-Safe Traceback Sanitizer (`prompt_manager.py`).
- Type-safe Telemetry Engine & Multi-Model Pricing Catalog (`telemetry.py`).

### Milestone 2: Deterministic Execution Harness [COMPLETED]
- Isolated transient subprocess sandbox (`sandbox.py`).
- Pre-execution AST syntax validator.
- Process-level timeout kill boundary (`EXECUTION_TIMEOUT_SECONDS = 3.0s`).
- Dynamic class method re-binding (`Class.method = method`).
- Zero-cost execution referee ($0.00 compute).

### Milestone 3: Live Model Dispatcher & Asymmetric Relay [COMPLETED]
- Zero-bloat unified REST API client for Gemini, OpenAI, Anthropic, DeepSeek (`model_dispatcher.py`).
- Deterministic offline mock simulation engine for offline testing.
- End-to-end asymmetric cascade coordinator enforcing the Anti-Anchoring Invariant (`relay_orchestrator.py`).
- Interactive Streamlit dashboard with Live Relay Runner tab (`app.py`).
- 45-point automated verification test suite with 100% pass rate (`test_milestones_2_3.py`, `test_itms.py`, `test_tdsc.py`).

### Milestone 4: Macro-Benchmark Evaluation & Container Hardening [NEXT PHASE]
- Formal evaluation harness against HumanEval (164 tasks) and MBPP (500 tasks).
- Empirical measurement of real-world pass rates, token reduction, and financial deltas across model combinations.
- Containerized execution sandbox (Docker / gVisor) for untrusted public network deployment.
