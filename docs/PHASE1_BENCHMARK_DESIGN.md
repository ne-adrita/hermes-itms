# Phase 1 Benchmark Design — ITMS Code-Editing Benchmark

**Scope:** the experimental contract for the Phase 1 runner.
**Status:** design and implementation complete; **the experiment has not been run**.
No live result exists yet. The task set is *synthetic and HumanEval-derived*;
this benchmark never produces a HumanEval pass rate.

---

## 1. Research question

For the same set of single-file Python function-repair tasks, how do three
editing strategies differ in

* the fraction of repairs that are correct,
* the reliability of their output contract,
* the resources they consume (tokens, wall-clock time, cost when reported),
* and how they fail?

The three strategies are **this project's own editing methods**. They are not
Aider, not Cline, and not any external agent. Where the historical code called
the search/replace method "Aider style", that label describes a *format*, not
an integration: no Aider or Cline code runs in this benchmark.

---

## 2. Fixed experiment variables

| Variable | Value | Notes |
|---|---|---|
| Dataset | the 20 accepted Phase 0 tasks `HE-EDIT-001`..`HE-EDIT-020` | `benchmarks/data/humaneval_edit_tasks.json`; task-set digest `92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1` (SHA-256 over the per-task mutated-source checksums) |
| Methods | `full_file_regeneration`, `search_replace_patch`, `itms_ast_splice` | implemented in `benchmarks/benchmark/adapters/` |
| Repetitions | 5 per (task, method) | 20 × 3 × 5 = **300 scheduled attempts** |
| Temperature | 0.2 | sent to the provider on every request; the provider may ignore it — no provider-side confirmation is available, so this is recorded as *requested*, not *verified* |
| Maximum output tokens | 2048 | same caveat: requested, not verified |
| Provider request timeout | 60 s (`--request-timeout`) | a request that exceeds it is recorded as a provider `timeout` failure |
| Official-test timeout | 10 s (`--evaluation-timeout`) | a candidate that hangs is recorded as a timeout failure |
| Model | one **pinned** model id for every method | `--model`; a dynamic alias (e.g. `openrouter/free`) is refused unless `--allow-dynamic-alias` is passed |
| Provider | one provider for every method | `--provider` |
| Correctness criterion | the official HumanEval `check(candidate)` of the task | identical for all three methods; the *candidate* is evaluated, never the reference |
| Scheduling | deterministic order derived from `--seed` | the same seed produces the same request order, which matters for rate-limited providers |

Nothing in the pipeline uses a hardcoded latency, a hardcoded token count or a
hardcoded cost. Latency is measured locally around every attempt; tokens and
cost are recorded only when the provider reports them.

### 2.1 What "pinned model" means in practice

The provider response contains a `model` field; the benchmark records it as
`served_model_id`. The requested id is recorded separately as
`requested_model_id`. If the two differ, the difference is visible in the
records and in the run metadata. The benchmark does **not** guess which free
model is available: `--model` must be given explicitly, and the runner fails
closed when it is missing.

### 2.2 Retries

The provider retry policy is explicit and bounded: `--max-retries` (default 2)
retries apply **only** to a transient HTTP 429 that is not a daily/per-minute
cap. The number of retries actually performed is recorded per attempt
(`retry_count`). Daily caps, 402 (out of credits) and every other failure are
never retried.

---

## 3. Treatment definitions

All three methods receive the same task (problem statement + repair
instruction) and the same mutated source, and all three are graded by the
same official test. They differ only in *how the edit is requested* and *how
the response is applied* — which is exactly the treatment.

### A. `full_file_regeneration`

*   Prompt: problem statement, repair instruction, and the complete mutated
    file between `----- BEGIN FILE -----` / `----- END FILE -----`.
*   Requested output: exactly one fenced `python` block containing the whole
    corrected module.
*   Application: the returned block becomes the candidate module verbatim.
*   Rejected when: no block (`no_code_block`), more than one block
    (`multiple_code_blocks`), empty response (`empty_response`), the block does
    not parse (`syntax_error`), the block does not define the target function
    (`target_function_missing`).

### B. `search_replace_patch`

*   Prompt: identical information to A (problem statement, instruction, whole
    mutated file).
*   Requested output: exactly one block of the form

        <<<<<<< SEARCH
        <exact lines copied verbatim from the file>
        =======
        <replacement lines>
        >>>>>>> REPLACE

*   Application: the project's existing
    `surgical_splicer.TextDiffPatcher.apply_search_replace` performs the
    replacement, so the benchmark measures the real patch-application
    behaviour (including its whitespace-mismatch and ambiguity failure modes).
*   Rejected when: the block is malformed (`malformed_patch`), there is more
    than one block (`multiple_patch_blocks`), the SEARCH text is absent
    (`search_block_not_found`), the SEARCH text occurs more than once
    (`ambiguous_search_block`), the patched file does not parse
    (`syntax_error`), the patched file loses the target function
    (`target_function_missing`).

### C. `itms_ast_splice`

*   Prompt: problem statement, repair instruction, and the **context skeleton**
    produced by the existing `skeletonizer.skeletonize_code` (target function
    name, `strip_docstrings=True`, `prune_unreachable=True`), plus the target
    node name.
*   Requested output: one fenced `python` block containing only the replacement
    for the target function — either the full `def` or only the indented body.
*   Application: the existing `surgical_splicer.ASTNodeLocator` locates the
    target and the existing `surgical_splicer.ASTSurgicalSplicer.splice`
    performs the splice. The rest of the file is preserved byte-for-byte by
    the splicer.
*   Rejected when: nothing can be extracted from the response
    (`no_code_block`), the target cannot be located (`splice_failed`), the
    spliced file does not parse (`syntax_error`), the spliced file loses the
    target function (`target_function_missing`).
*   **Known limitation (preserved, not fixed):** a *body-only* replacement that
    declares a nested function is hoisted to module level by the existing
    splicer, producing a syntactically valid but semantically wrong file that
    `splice()` still reports as successful. See `BENCHMARK_READINESS_AUDIT.md`
    §3. Phase 1 does not change the splicer; the behaviour is characterised by
    a test (`test_phase1_adapters.py::TestItmsSpliceAdapter::test_known_nested_function_limitation_is_preserved`).

---

## 4. Prompt and output differences (documented confounders)

The treatments are not perfectly symmetric, and pretending otherwise would
overstate the comparison. The known asymmetries are:

| Asymmetry | Consequence |
|---|---|
| ITMS shows a *skeletonized* context instead of the raw file | ITMS tokens are structurally smaller; this is the treatment being measured, but it means token comparisons measure the treatment, not only the editor's efficiency |
| Output contracts differ (whole file vs. patch vs. node) | output token counts are not directly comparable across methods; they measure the contract |
| ITMS can only change the target function | a defect that requires changing module-level code cannot be repaired by method C; the Phase 0 task set contains no such task, but the constraint is real |
| Search/replace requires an exact, unique match | the method fails on whitespace drift — that failure mode is part of the treatment |

The underlying task, the correctness oracle and the model are identical across
methods, so the comparison remains a paired within-task comparison.

---

## 5. Avoiding confounding

*   No hardcoded latencies, token counts or costs exist anywhere in the Phase 1
    code. (The pre-existing `edit_orchestrator.py` does contain hardcoded
    latencies of 1650/520/185 ms and pre-written "simulated" model outputs;
    that file is **not** used by the Phase 1 runner. It remains a legacy
    simulation and is documented as such.)
*   The three adapters share one evaluation path: the same official test, the
    same isolated sandbox, the same timeout, the same correctness decision.
*   The runner uses the provider's real response in live mode. A live failure
    is recorded as a failure; it is never replaced by a mock response.
*   Mock mode exists only to exercise the pipeline and is labelled
    `mode=mock`, `is_measurement=false` in every artefact it produces.

---

## 6. Evaluation and isolation

*   The candidate module is written into a fresh temporary workspace, which is
    removed in a `finally` block (also after an exception).
*   The official test is executed by `benchmarks/phase0/runner.py`, which runs
    the candidate plus the official `check(candidate)` in a throw-away
    directory with a scrubbed environment (`PYTHONPATH` is replaced by the
    sandbox directory only), user site-packages disabled, no injected
    convenience imports, and sockets blocked at the interpreter level via
    `sitecustomize`.
*   The pre-existing `sandbox.py` was **not** used, because it injects
    convenience imports into the evaluated script and splices the candidate
    into the context in a way that is specific to the legacy cascade flow.
    Using it would have introduced a confound between methods and would have
    violated the "no convenience imports" rule.
*   The isolation is a **best-effort sandbox for a benchmark workload, not a
    security boundary**. It is important to be precise about what is
    *enforced* and what is not:

    | Control | Status | Detail |
    |---|---|---|
    | scrubbed environment | **enforced** | no API key, token or secret variable is present in the evaluated process |
    | isolated working directory | **enforced** | a fresh temporary directory per evaluation |
    | no accidental project imports | **enforced** | `PYTHONPATH` points only at the sandbox; `import sandbox` fails |
    | wall-clock timeout | **enforced** | the child process is killed when the limit is exceeded |
    | no injected convenience imports | **enforced** | the candidate is evaluated exactly as returned |
    | socket guard | **best-effort** | it blocks `socket.socket()` in the evaluated process, but a candidate that spawns a child interpreter with `PYTHONPATH` removed **can** open a socket (verified empirically) |
    | filesystem containment | **absent** | the candidate can read any file the process can read (including the repository and `.env`) and can write anywhere writable |
    | process containment | **absent** | the candidate may spawn arbitrary child processes |
    | credential protection inside the benchmark | **enforced** | credentials never enter the candidate's environment and are never written to an artefact; the candidate's stdout/stderr are not recorded |

    The benchmark therefore assumes **non-hostile model output**. A candidate
    that deliberately probes the machine is out of scope, and the sandbox must
    not be described as a hardened jail.

---

## 7. Scheduling, denominators and analysis

*   The schedule is the full cross product; a failed attempt stays in the
    denominator. See `docs/PHASE1_METRICS_AND_ANALYSIS.md`.
*   The unit of analysis is the **task** (20 tasks). The five repetitions are
   repeated observations of one task, not five independent tasks.
*   Comparisons are paired at the task level and reported with a bootstrap
   interval over tasks.

---

## 8. Outputs

One experiment writes to one dedicated directory (never into `benchmarks/data/`):

```
<output-dir>/
    attempts.jsonl        # one JSON object per attempt, written incrementally
    attempts.csv          # the same attempts, flat columns
    summary.json          # aggregated metrics + paired comparisons
    run_metadata.json     # configuration, task digest, model id, versions
    artifacts/            # per-attempt audit trail (see below)
        <attempt_id>/
            attempt.json      # identity, digests, evaluation outcome
            raw_response.json # redacted provider response (if a response exists)
            candidate.py      # the exact bytes evaluated (if a candidate exists)
```

### 8.1 Artefact trail

Every attempt gets its own directory, so a reviewer can reproduce the
evaluation independently:

*   `candidate.py` holds the candidate that was submitted to the official
    evaluator. Two digests are recorded and they are **not** interchangeable:
    *   `candidate_sha256` — the SHA-256 of the **exact bytes submitted to
        the official evaluator**. This is the provenance anchor and it never
        changes; `attempt.json → evaluation.candidate_sha256` records the
        same value, so the evaluation outcome is bound to the exact bytes.
    *   `candidate_file_sha256` — the SHA-256 of the bytes **actually stored**
        in `candidate.py`. It equals `candidate_sha256` in the normal case.
        They differ **only** when the stored copy had to be redacted, in
        which case `candidate_redacted` is `true` and the stored bytes are
        *not* identical to the evaluated bytes — the run must not claim
        otherwise.
*   `raw_response.json` holds the provider response **after secret
    redaction**, together with the digest of the original text.
*   `attempt.json` ties the artefacts together: the attempt identity
    (attempt id, task, method, repetition, requested and served model ids),
    both candidate digests, and the official-evaluation outcome.
*   **Redaction policy.** One rule set drives both *detection* and
    *redaction* (`benchmarks/benchmark/provider.py`), so the two cannot
    drift apart. It covers `authorization: Bearer …`, other `Authorization`
    schemes (`Basic`, `Digest`, `Negotiate`, `Token`, …), `Bearer …`,
    `sk-…`, `or-…`, `x-api-key=`, `api_key=`, `token=`, credential-named
    assignments (`password=`, `passwd=`, `secret=`, `client_secret=`),
    AWS-style `AKIA…` access-key identifiers, JWT-like `eyJ…` strings,
    Slack-like `xoxb-…` tokens and URL basic authentication
    (`https://user:password@host/path`). The bare key patterns are
    additionally matched in an **unanchored, high-entropy** form so a
    credential token directly adjacent to a word character (e.g.
    `prefixsk-live-…`) is still caught; the length requirement keeps ordinary
    prose (`task-oriented`) and ordinary source code
    (`password = os.getenv("PW")`) intact. The stderr tail is **redacted
    before** it is truncated, so a truncation can never cut a credential into
    an unmatchable fragment. Credential-like *keys* are dropped from
    structured metadata. The candidate is stored verbatim whenever possible
    (its digest must match the evaluated bytes); it is scanned with the same
    detector and, if anything credential-shaped is found, the stored copy is
    redacted and the attempt is flagged. In practice the model is never given
    a credential, so that branch is not expected to trigger. Detection is
    **heuristic**: it recognises credential *shapes*, and a credential in an
    unrecognised format may not be detected.
*   `candidate.py` is written **only** when a candidate actually existed. An
    attempt that failed to parse has `candidate_state="none"` and no
    candidate file — no candidate is ever fabricated. A candidate that was
    produced but failed the official test *is* persisted, and the record
    distinguishes the two cases (`candidate_state` plus `test_passed`).
*   Artefact files are written **atomically** (temporary file + rename), so a
    failure can never leave a half-written file.
*   A persistence failure is never silent: the attempt is demoted to an
    `incomplete` / `internal_error` record, so a missing artefact can never
    be reported as a successful benchmark result, and the partially written
    directory stays **discoverable** through the record's `artifacts_path`
    (with `artifacts_status="failed"`).
*   A re-used output directory gets a run-scoped `artifacts-<run_id>` root,
    so two runs never share an artefact and an earlier run is never
    overwritten.

The output directory must be new or empty; an existing non-empty directory is
refused unless `--allow-overwrite` is passed explicitly. Resuming an
incomplete experiment is refused: the runner cannot prove which attempts
already reached the provider, so it cannot guarantee that no request is
duplicated.

---

## 9. Safety model

*   Default mode is `mock`; a live provider is never contacted unless
    `--mode live` is passed.
*   Live mode fails closed when the provider, the credential or the pinned
    model id is missing, and when a dynamic model alias is used without an
    explicit opt-in.
*   Live mode prints the planned number of provider calls and requires an
    interactive `yes` confirmation. `--yes` skips the prompt but still
    requires the explicit `--mode live` command.
*   `--dry-run` validates the configuration and prints the schedule without
    contacting any provider.
*   No credential is ever printed or logged: error messages pass through a
    secret-redaction filter before they are stored.
