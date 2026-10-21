# Phase 3.1 — Independent Artifact Persistence Audit

**Project:** Hermes / ITMS code-editing benchmark
**Repository:** `/Users/noshinebnatadrita/Documents/499B`
**Audited artefact:** the Phase 3.1 artefact-persistence implementation
(`benchmarks/benchmark/artifacts.py` and its integration)
**Audit date:** 2026-10-20
**Final independent review:** 2026-10-21 — see **§11** for the re-verification
and the three small corrections made in that review.
**Audit type:** independent, read-only source inspection + offline execution
**Live requests made during this audit:** 0 (no provider was contacted; the
Phase 3 pilot was not re-run; the 300-attempt benchmark was not started)
**Live requests made during the final review:** 0 (same).
**Status:** audit complete **and remediated** — see §10 for the remediation
record (fixes, test counts, remaining limitations) and §11 for the final
review.

---

## 0. Verdict

# PASS WITH LIMITATIONS

*(Verdict as issued by the audit. The two Major findings and both Minor
findings have since been remediated — see §10. The verdict is unchanged
because the residual limitations in §7 still apply.)*

The persistence mechanism itself is **correct and verified**: the bytes handed
to the official evaluator are byte-identical to the persisted candidate and to
every recorded digest; run isolation holds; a persistence failure can never be
reported as a success; and the full artefact trail is internally consistent.

Two **Major** findings limit it:

| ID | Finding |
|---|---|
| **M-A** | The credential-redaction patterns are **word-boundary anchored**, so a credential-shaped token that directly follows a word character is stored **verbatim** in `candidate.py` and in `raw_response.json`, and `candidate_redacted` stays `false` (no signal is raised). |
| **M-B** | The **trigger** pattern set that decides whether a candidate is redacted (`artifacts._CREDENTIAL_SHAPED`) is **narrower than** the redaction rule set (`provider._SECRET_RULES`): an `api_key=` / `token=` assignment inside a candidate is stored verbatim, although the same text in the raw response *is* redacted. |

Both are properties of the *secret-handling* control, not of the persistence
logic. The **practical exposure today is nil** — the model is never given a
credential, the evaluated environment is scrubbed, and the artefacts stay
local — but the documented guarantee ("credential values and authorization
headers do not appear in saved artifacts") is **not** what the code actually
enforces, and my own Phase 3.1 report asserted it too strongly (its test only
covered a space-delimited secret).

Two **Minor** findings complete the list: the stderr tail is truncated
*before* redaction (so a cut secret can leave a fragment), and the
`candidate_sha256` semantics break the "stored bytes == evaluated bytes"
invariant in the candidate-redaction branch.

**Recommendation on a Phase 3 live-pilot rerun:** *conditionally safe*. The
persistence path is verified offline and does not alter the evaluation, so a
rerun would not be scientifically compromised. However, M-A and M-B are small,
offline-testable patches, and because a live run writes **real provider
output** to disk, I recommend fixing them (plus the two Minor items) before
the next live run. If the operator accepts the residual risk — the model never
sees a credential, and the artefacts never leave the machine — a rerun is
acceptable as-is.

---

## 1. Checks actually executed

| # | Check | Command / probe | Observed result |
|---|---|---|---|
| 1 | Full offline test suite (network blocked) | `PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q` | **317 passed, 23 subtests passed** in 34.72 s |
| 2 | Artefact-persistence suite | `pytest -q benchmarks/tests/test_phase3_artifacts.py` | **13 passed** (1.58 s) |
| 3 | Phase 2 remediation suite | `pytest -q benchmarks/tests/test_phase2_remediation.py` | **40 passed** (3.33 s) |
| 4 | Phase 0 suite | `pytest -q benchmarks/phase0/test_phase0_tasks.py` | **29 passed** (5.44 s) |
| 5 | Phase 0 manifest | `python benchmarks/phase0/validate_tasks.py --verify-manifest` | `tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)`, exit 0 |
| 6 | **Evaluator-boundary provenance** | probe monkeypatching `benchmarks.phase0.runner.run_official_test` to hash the bytes it receives | evaluator bytes == `record.candidate_sha256` == `sha256(candidate.py)` == `attempt.json candidate_sha256` == `attempt.json evaluation.candidate_sha256`; byte length 223 == 223 |
| 7 | **Redaction branch / digest contradiction** | probe with a credential-shaped token inside the candidate | `record.candidate_sha256` ≠ `sha256(stored candidate.py)`; `candidate_redacted=True`; **`candidate_file_sha256` absent from the record and from the CSV** (present only in `attempt.json`) |
| 8 | **Secret leakage, common positions** | probe injecting a synthetic secret into the response text, the provider error message and the candidate stderr | secret **absent** from all artefacts in all three channels |
| 9 | **Secret leakage, word-adjacent position** | probe injecting the same secret after 100 word characters | secret **present verbatim** in `candidate.py` **and** `raw_response.json`; `candidate_redacted=False` |
| 10 | **Pattern-set drift** | direct comparison of `provider._SECRET_RULES` vs `artifacts._CREDENTIAL_SHAPED` | `api_key=`/`token=` forms are redacted by the rules but do **not** trigger candidate redaction |
| 11 | **Run isolation** | two runs with `allow_overwrite=True` in one directory | run 1 → `attempts.jsonl` + `artifacts/`; run 2 → `attempts-<run_id>.jsonl` + `artifacts-<run_id>/`; distinct run ids; run 1's `attempt.json` contains no reference to run 2; run 1's candidate still holds run 1's content |
| 12 | **Persistence failure** | probe failing the 2nd artefact write | record demoted to `incomplete` / `internal_error`, `artifacts_status="failed"`, **not** `repair_success`; JSONL↔CSV **0 mismatches**; attempt still in the denominator (`recorded=1, planned=1`); one **orphan** artefact directory left behind and unreferenced |
| 13 | **No fabricated candidate** | probe where the response omits the target function / the code block | `candidate_state="none"`, `candidate_path=None`, no `candidate.py` written |
| 14 | **Evaluation semantics unchanged** | source inspection of `pipeline._run_official_test` and the record field assignments; mtimes of the protected files | evaluation call, scoring fields, retry policy, token/cost fields and requested/served model ids are unchanged; adapters (18:18–18:19), `surgical_splicer.py` (13:23), `sandbox.py` (16:38), `skeletonizer.py` (16:38), Phase 0 data (18:13–18:22) all untouched |
| 15 | **No provider contact** | HTTP-client guard probe; all probes used fake providers | no `httpx.Client` was constructed; no network request was issued |

**No live provider request was made during this audit.** Every probe used a
fake provider object; the real Phase 3 pilot was not re-run and its artefacts
were not modified.

---

## 2. Candidate provenance — **VERIFIED, with one contradiction (M-A / §3.2)**

### 2.1 The normal path is exact

`benchmarks/benchmark/pipeline.py:202` evaluates
`adapter_result.candidate_source`; `artifacts.py:184-190` encodes **that same
string**; `artifacts.py:196` records its digest. The probe in §1.6 confirms
the whole chain at the byte level:

```
sha256(bytes given to official evaluator) : 1195620e0950944ee...9be0
sha256(record.candidate_sha256)           : 1195620e0950944ee...9be0
sha256(stored candidate.py bytes)          : 1195620e0950944ee...9be0
attempt.json evaluation.candidate_sha256   : 1195620e0950944ee...9be0
byte length evaluator/file                 : 223 223
```

The evaluation outcome is therefore bound to an exact, independently
verifiable byte sequence: a reviewer can hash `candidate.py` and compare it
with the digest in the JSONL, the CSV and the per-attempt `attempt.json`.

### 2.2 Behaviour in the four edge cases

| Case | Behaviour | Evidence |
|---|---|---|
| no candidate (parse failure / target function missing) | `candidate_state="none"`, `candidate_path=None`, **no `candidate.py` written**; the raw response and `attempt.json` are still written | `artifacts.py:182` (`if candidate is not None`), probe §1.13 |
| parsing fails | attempt recorded as `output_parse_error` / `truncated_output`; raw response retained for audit | `pipeline.py:131-179` |
| evaluation fails (candidate wrong) | the candidate **is** persisted; `attempt.json` carries `evaluation.passed=false` plus the failure `error_type`, `duration_ms` and the redacted stderr tail | `artifacts.py:181-198`, §1.6 |
| artefact persistence fails | the attempt is **demoted** to `incomplete` / `internal_error` with `artifacts_status="failed"`; it can never be counted as a success, and it stays in the denominator | `experiment.py:243-262`, probe §1.12 |

### 2.3 The contradiction in the redaction branch

`artifacts.py:186-198`: when the candidate contains a credential-shaped
string, the **stored** bytes are redacted while `info["candidate_sha256"]`
remains the digest of the **evaluated** bytes:

```
record.candidate_sha256 (digest of EVALUATED bytes) : 606156ea8c4a141f...
sha256(stored candidate.py)                         : c5f826b64c9ac6f9...
digests equal?                                      : False
record.candidate_redacted                            : True
attempt.json candidate_file_sha256                  : c5f826b64c9ac6f9...
record has a 'candidate_file_sha256' column?        : False
CSV has candidate_file_sha256 column?               : False
```

So the invariant *"the persisted candidate bytes are exactly the bytes
submitted to the official evaluator"* is **false in this branch**, and a
reviewer working from the JSONL/CSV alone sees a digest that does not match
the file. The information needed to reconcile it exists — but only in
`attempt.json`, and only if the reviewer knows to look there. The trade-off
itself is unavoidable (storing the raw bytes verbatim would leak the
credential); the defect is that it is resolved **silently in one direction**
and is not representable in the record.

**Proposed smallest patch (not applied — see §6.1):** add
`candidate_file_sha256` to `AttemptRecord` and to `CSV_COLUMNS`, keep
`candidate_sha256` as the digest of the *evaluated* bytes (the provenance
anchor), and state the two-digest semantics explicitly in
`docs/PHASE1_BENCHMARK_DESIGN.md` §8.1. No methodology change.

---

## 3. Secret handling — **M-A (Major), M-B (Major), plus a Minor**

### 3.1 What works (verified)

*   The raw provider response is redacted **before** it is written
  (`artifacts.py:154`, `provider.redact_secrets`).
*   Provider error messages are redacted at the pipeline boundary
  (`pipeline.py:120`, `:238`).
*   The candidate's stderr tail is redacted (`pipeline.py:231`).
*   Structured metadata is scrubbed and credential-like **keys** are dropped
  (`artifacts.py:70-84`).
*   With a space/quote/`=`-delimited synthetic secret, the secret was
  **absent from every artefact** in all three channels (probe §1.8).
*   No secret, environment variable or authorization header is ever written;
  only the header *name* exists in the client.

### 3.2 M-A (Major) — word-boundary anchoring defeats the redaction

`provider.py:87-88` use `\bsk-…` / `\bor-…`, i.e. the pattern only matches
when the token starts at a word boundary. A credential-shaped token that
directly follows a word character is therefore **not** matched:

```
string start             redacted=True
after '='                redacted=True
after quote              redacted=True
after space              redacted=True
after a word character   redacted=False   <-- 'prefixsk-live-FAKE…'
after a digit            redacted=False   <-- 'id123sk-live-FAKE…'
```

End-to-end consequence (probe §1.9): with the secret placed after 100 word
characters, it was stored **verbatim** in *both* `candidate.py` and
`raw_response.json`, and `candidate_redacted` remained `false`, so nothing
in the record signals that anything was unusual.

Practical exposure today: **nil** (no credential is ever shown to the model,
and the evaluated environment is scrubbed). The finding is that the
*documented control* does not hold as written.

### 3.3 M-B (Major) — the candidate-redaction trigger is narrower than the redaction rules

`artifacts.py:186` decides whether to redact the candidate with
`_CREDENTIAL_SHAPED` (`artifacts.py:57-60`), while the actual redaction is
performed by `provider._SECRET_RULES` (`provider.py:84-92`). The two sets are
independent and have drifted:

```
api_key= form (no sk- prefix)    candidate-redaction triggered=False | redact_secrets removes it=True
token= form                      candidate-redaction triggered=False | redact_secrets removes it=True
bare sk- key                     candidate-redaction triggered=True  | redact_secrets removes it=True
word-adjacent sk- key            candidate-redaction triggered=False | redact_secrets removes it=True
```

So a candidate containing `api_key="…"` or `token="…"` is stored verbatim,
although the *same* text inside the raw response is redacted. Two independent
pattern sets guarding the same artefact class is the underlying design smell.

### 3.4 Minor — the stderr tail is truncated *before* redaction

`pipeline.py:273` stores `(outcome.stderr or "")[-500:]` and the redaction is
applied afterwards (`pipeline.py:231`). A secret that straddles the 500-char
window boundary is cut in half, and the surviving fragment no longer matches
any pattern (the `sk-` prefix is in the discarded part) — so it is stored
verbatim. The pipeline order is therefore *truncate → redact*; the safe order
is *redact → truncate*.

### 3.5 Channels checked and found clean

*   filenames: only the `attempt_id` (a `uuid4().hex`) is used in a path; no
    task id, method name or model id is interpolated into a path, so there is
    no path-injection or traversal surface;
*   JSONL / CSV / summary: they carry paths and digests, never the response
    text or the candidate source (so they stay small);
*   `raw_usage`: integers only;
*   `parse_error` / `error_message`: redacted at the pipeline;
*   `attempt.json`: written through `_scrub_mapping` (`artifacts.py:70`),
    which drops credential-like keys and redacts remaining strings.

---

## 4. Run isolation — **VERIFIED (one Minor)**

| Property | Result | Evidence |
|---|---|---|
| Two runs in one directory | run 1 → `attempts.jsonl` + `artifacts/`; run 2 → `attempts-<run_id>.jsonl` + `artifacts-<run_id>/` | probe §1.11 |
| Cross-references | none: run 1's `attempt.json` contains no reference to run 2 | probe §1.11 |
| Earlier run's data | untouched: run 1's candidate still holds run 1's content | probe §1.11 |
| Run ids | distinct per run; `attempt.json` carries the owning `run_id` | probe §1.11 |
| Store-level overwrite protection | `artifacts.py:127-131` raises `ArtifactPersistenceError` if the attempt directory already exists | `test_phase3_artifacts.py::TestNoOverwriting::test_the_store_refuses_to_overwrite_an_existing_attempt_directory` |
| `allow_overwrite` semantics | only relaxes the "non-empty directory" refusal; it does **not** permit sharing files or artefact roots | `experiment.py:163-172`, `artifacts.py:95-102` |

**Minor — orphan artefact directory.** When persistence fails *after* some
files were written (probe §1.12), the partially written attempt directory
remains on disk while the record's `artifacts_path` stays `None` (the record
is demoted before the info mapping is merged). The directory is unreferenced
by any record, so it cannot mislead a reader of the JSONL, but a reviewer
browsing the tree will find a directory that no record points to.

---

## 5. Evaluation integrity — **VERIFIED (unchanged)**

*   The evaluation call is untouched: `pipeline.py:258-266` still calls
    `benchmarks.phase0.runner.run_official_test(candidate_source,
    task.official_test, task.target_function, timeout=…)` with the same
    arguments as before Phase 3.1.
*   Scoring is unchanged: `record.test_passed`, `record.test_error_type`,
    `record.timed_out` are still set from the evaluator's result
    (`pipeline.py:203-205`).
*   Retry policy is unchanged: `record.retry_count` is still taken from the
    provider layer (`pipeline.py:123`, `:140`); the dispatcher was not touched.
*   Token/cost semantics are unchanged: provider-reported counts and local
    estimates remain separate fields; missing cost stays `null`
    (`pipeline.py:142-149`).
*   `requested_model_id` and `served_model_id` remain separate
    (`pipeline.py:136-138`).
*   Protected files are untouched (mtimes): the three adapters
    (Oct 20 23:18–23:19), `surgical_splicer.py` (13:23), `sandbox.py`
    (16:38), `skeletonizer.py` (16:38), `benchmarks/phase0/runner.py`
    (18:11) and every Phase 0 data file (18:13–18:22).
*   The official tests themselves are unchanged: Phase 0 manifest verifies
    (88 files, 0 mismatches).

### 5.1 Implementation test vs. provider experiment

Everything in this audit is an **implementation test**: it used fake
provider objects, temporary directories and the local sandbox. It is *not* a
provider experiment, it says nothing about model quality, and no live request
was made. The Phase 3 pilot's own artefacts remain the only live evidence in
this repository, and they are unmodified.

---

## 6. Proposed minimal patches (reported, **not applied**)

Per the audit instructions, no code was changed. These are the smallest
patches that close the findings; each is offline-testable.

### 6.1 M-A / M-B — one shared, unanchored-but-high-entropy redaction set

*   `benchmarks/benchmark/provider.py:84-92` — keep the existing rules, but
    add **unanchored, high-entropy** variants, e.g.
    `sk-[A-Za-z0-9]{20,}` and `or-[A-Za-z0-9]{20,}` (real keys are ≥32
    characters), so an embedded token is still caught while ordinary words
    such as `task-oriented` are not over-redacted.
*   `benchmarks/benchmark/artifacts.py:57-60` — **delete** the second pattern
    set and reuse the provider's rules, so the trigger and the redaction can
    no longer drift. The candidate should be redacted with the *same*
    function that redacts the response.

### 6.2 Minor — redact before truncating the stderr tail

*   `benchmarks/benchmark/pipeline.py:258-273` — return the full stderr from
    `_run_official_test` and apply `redact_secrets` **before** slicing the
    last 500 characters (`pipeline.py:231`), so a cut secret cannot leave a
    fragment.

### 6.3 Minor — make the redaction branch representable in the record

*   `benchmarks/benchmark/records.py` — add `candidate_file_sha256` to
    `AttemptRecord` and to `CSV_COLUMNS`;
*   `benchmarks/benchmark/artifacts.py:198` — return it in `info`;
*   `docs/PHASE1_BENCHMARK_DESIGN.md` §8.1 — state that `candidate_sha256`
    is the digest of the **evaluated** bytes and `candidate_file_sha256` the
    digest of the **stored** bytes, which differ only when redaction
    occurred.

### 6.4 Minor — reference (or remove) orphan artefact directories

*   `benchmarks/benchmark/experiment.py:243-262` — on a persistence failure,
    either set `record.artifacts_path` to the partially written directory
    (so it is discoverable) or delete it. Today it is left behind and
    unreferenced.

---

## 7. Remaining limitations

1. **The Phase 3 pilot's artefacts cannot be recovered.** They were never
   written; the pilot remains `PASS WITH LIMITATIONS` with an unreproducible
   candidate. Nothing in this audit changes that.
2. **The secret-handling guarantee is position-dependent** (§3). Until
   §6.1/§6.2 are applied, the accurate statement is: *credential-shaped
   tokens that are delimited by whitespace, quotes or `=` are redacted;
   word-adjacent tokens, `api_key=`-style assignments inside a candidate, and
   fragments created by stderr truncation are not.*
3. **The candidate redaction breaks byte-equality** in one branch (§2.3);
   the two-digest representation is not yet in the record.
4. **No size cap** on the persisted response or candidate; a 300-attempt run
   stores 900 files with no retention policy.
5. **Orphan artefact directories** can survive a failed write (§4).
6. The evaluation sandbox remains **best-effort** isolation (no filesystem or
   process containment; the socket guard is bypassable by a child process).
   Persisting the candidate does not change that.
7. All findings are about *local* artefacts; nothing is uploaded anywhere.

---

## 8. Recommendation on a Phase 3 live-pilot rerun

**Conditionally safe — safe after four small, offline-testable patches.**

*   The persistence layer itself is verified: byte-exact candidate
    provenance, correct digests, run isolation, surfaced persistence
    failures, no fabricated candidates, and internally consistent
    JSONL/CSV/summary/metadata. It does **not** alter the evaluation, the
    scoring, the retry policy or the token/cost semantics.
*   A live rerun would therefore produce scientifically comparable data.
*   Before writing real provider output to disk, apply §6.1–§6.4 and re-run
    the offline suite plus the Phase 0 manifest check. The patches touch
    only the redaction helpers and the record schema — **not** the methods,
    the evaluation, the task set or the experiment design.
*   If the operator prefers not to wait, a rerun is acceptable **as-is**
    provided the operator accepts that (a) the model never sees a credential,
    so the residual exposure is theoretical, and (b) the artefacts remain
    local and are not committed.
*   A live rerun still requires a **pinned, non-alias** model id if the goal
    is to compare methods; the Phase 3 pilot's `openrouter/free` alias served
    two different models and therefore cannot support a comparison.

**Phase 4 (the 300-attempt experiment) remains out of scope and was not
started.**

---

## 10. Remediation record (added after the audit)

All four findings were remediated with the smallest possible changes. **No
live provider request was made, the Phase 3 pilot was not re-run, and the
300-attempt benchmark was not started.** The task definitions, gold
solutions, official tests, adapters, splicer algorithm, evaluation/scoring,
retry policy, token/cost semantics and provider/model configuration were
**not** changed.

### 10.1 Files changed

| File | Change |
|---|---|
| `benchmarks/benchmark/provider.py` | **M-A / M-B** — added three *unanchored, high-entropy* rules (`sk-[A-Za-z0-9_-]{20,}`, `or-[A-Za-z0-9_-]{20,}`, `bearer\s+[A-Za-z0-9._-]{20,}`) and a new `contains_credential()` detector **derived from the same rule set** that `redact_secrets()` applies, so detection and redaction can no longer drift. |
| `benchmarks/benchmark/artifacts.py` | **M-B** — deleted the second pattern set (`_CREDENTIAL_SHAPED`) and now calls `provider.contains_credential()`. **Minor** — artefact writes are now **atomic** (temp file + `os.replace` + `fsync`), so a failure cannot leave a half-written file; added `attempt_dir()` / `relative_attempt_dir()` helpers. **Provenance** — `candidate_file_sha256` is returned in the record mapping. |
| `benchmarks/benchmark/records.py` | **Provenance** — new `candidate_file_sha256` field plus the matching CSV column, with the two-digest semantics documented in the field docstring. |
| `benchmarks/benchmark/pipeline.py` | **Stderr** — the stderr tail is now **redacted before** it is truncated to the last 500 characters (`pipeline.py:275`). |
| `benchmarks/benchmark/experiment.py` | **Partial persistence** — on a persistence failure the attempt is demoted to `incomplete`/`internal_error` *and* the partially written attempt directory is recorded in `artifacts_path` (with `artifacts_status="failed"`), so it is discoverable instead of an orphan. |
| `benchmarks/benchmark/analysis.py` | **Accounting** — a repair now counts as successful only when the attempt's status is `repair_success`; an attempt demoted for an artefact-persistence failure is never counted as a success, even when the official test happened to pass before the demotion. (This closes a hole the remediation's own regression test exposed: the task-level metric previously used `test_passed` alone.) |
| `benchmarks/tests/test_phase3_artifacts.py` | 20 new regression tests (13 → 33). |
| `docs/PHASE1_BENCHMARK_DESIGN.md` | §8.1 rewritten: the two-digest semantics, the single-rule-set redaction policy, atomic writes and the partial-directory policy. |
| `docs/PHASE3_1_ARTIFACT_AUDIT.md` | This section. |

### 10.2 Remediation verification — checks actually executed

| Check | Result |
|---|---|
| Credential detection + redaction in 8 positions (start, `=`, quote, space, **word-adjacent**, digit-adjacent, URL query, `Bearer`) | all detected and redacted; the synthetic secret is fully removed in every case |
| `api_key=` / `token=` assignment forms | detected (`contains_credential` → `True`) and redacted — previously **not** detected for the candidate trigger |
| Benign prose (`task-oriented`, `risk-oriented`, `sk-1`) | not detected, not redacted |
| Detection/redaction parity | `contains_credential(s) == ("[REDACTED]" in redact_secrets(s))` for all samples; the artefact module no longer owns a pattern set (`_CREDENTIAL_SHAPED` removed) |
| Secret present in any persisted artefact (candidate, raw response, stderr, `attempt.json`, JSONL, CSV, summary) | **False** — previously **True** for the word-adjacent case |
| Evaluator-boundary provenance (normal path) | evaluator bytes == `record.candidate_sha256` == `sha256(candidate.py)` == `attempt.json candidate_sha256` == `evaluation.candidate_sha256` (223 == 223 bytes) |
| Redacted candidate path | `candidate_sha256` != `candidate_file_sha256`, `candidate_redacted = true`, both digests present in the record **and** the CSV |
| Stderr truncation splitting a synthetic credential | the fragment does **not** survive (`"FAKE0123456789abcdef" not in tail`); the full secret is absent from every artefact |
| Persistence failure at write stages 1, 2 and 3 (`test_failures_at_different_write_stages_are_surfaced`, parametrised 1/2/3) | attempt demoted (`incomplete` / `internal_error`), `artifacts_status="failed"`, JSONL↔CSV consistent, **not** counted as a repair success (`repair_success.count == 0`, `tasks_with_at_least_one_success == 0`, `candidate_produced_attempts == 0`) |
| Demotion after a *passing* official test (`test_a_demoted_attempt_is_not_counted_as_a_task_pass`) | `test_passed is True` while `status == incomplete` and `artifacts_status="failed"`; `repair_success.count == 0`, `tasks_with_at_least_one_success == 0`, `mean_task_pass_rate == 0.0`, `per_task_pass_rate == 0.0`; denominators unchanged (`scheduled == recorded == planned == 1`); no `.tmp` file left behind; the partial directory is referenced by the record |
| Run isolation (two runs, one directory) | run 1 → `attempts.jsonl` + `artifacts/`; run 2 → `attempts-<run_id>.jsonl` + `artifacts-<run_id>/`; no cross-references; the first run's data untouched |

### 10.3 Test counts after the remediation

```
benchmarks/tests/test_phase3_artifacts.py  ->  33 passed   (was 13)
PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q
                                            ->  337 passed, 23 subtests passed  (was 317)
python benchmarks/phase0/validate_tasks.py --verify-manifest
                                            ->  tasks: 20 / passed: 20 / failed: 0
                                                manifest: OK (88 files)   exit 0
```

The +20 tests are: 8 positional redaction cases, 2 key-assignment cases,
3 benign-prose cases, 1 rule-set-parity case, 1 no-secret-in-any-artefact
case, 3 persistence-failure stages, 3 provenance/digest cases, 1
stderr-truncation case (plus the adjusted assertion in the existing
persistence-failure test).

### 10.4 Remaining limitations after the remediation

1. The **redaction guarantee is now position-independent** for credential
   *tokens*, but a redacted candidate is still not byte-identical to the
   evaluated bytes. That is inherent (storing the raw bytes would leak) and
   is now *represented* rather than hidden: `candidate_sha256` (evaluated
   bytes) and `candidate_file_sha256` (stored bytes) are both in the
   record, the CSV and `attempt.json`, and `candidate_redacted` flags the
   difference. **No claim of byte equality is made in the redacted case.**
2. The unanchored high-entropy rules can over-redact a pathological benign
   token (e.g. a 26-character word starting with `sk-`). The length
   threshold keeps ordinary prose intact.
3. A partially written attempt directory is now *discoverable* rather than
   deleted: the record points at it and marks `artifacts_status="failed"`.
   A reviewer will find a directory without a complete `attempt.json`; the
   record's status is the authority.
4. The **Phase 3 pilot's artefacts cannot be recovered** — they were never
   written. The pilot remains `PASS WITH LIMITATIONS`.
5. No size cap on the persisted response or candidate; a 300-attempt run
   stores 900 files with no retention policy.
6. The evaluation sandbox remains **best-effort** isolation; persisting the
   candidate does not change that.
7. `contains_credential()` is a heuristic: it recognises credential
   *shapes*, not credentials. A credential in an unrecognised format (for
   example a JWT or a bare hex token) would not be detected. The practical
   protection is that the model is never given a credential at all.

### 10.5 Updated recommendation on a Phase 3 live-pilot rerun

**Safe to consider.** The four findings are closed, the redaction guarantee
now holds for every position tested, the candidate provenance is explicit
and verifiable, and the persistence-failure path cannot produce a misleading
success. The live path is still unproven against a real gateway, and the
scientific caveat from the Phase 3 report stands: a comparative run needs a
**pinned, non-alias** model id, because `openrouter/free` served two
different models during the pilot.

A live-pilot rerun still requires the operator's explicit go-ahead; it was
**not** started as part of this remediation.

### 10.6 Final fix — the one metric the first remediation pass missed

A final read-only review of the remediation found that the success-accounting
change had **not** been applied to one metric: `analysis.py`'s
`task_pass_rates` still used `r.test_passed`, while `solved_tasks`,
`repair_success` and the paired comparison already used
`status == AttemptStatus.REPAIR_SUCCESS`. The consequence was an internal
contradiction inside the same `task_level` block: an attempt demoted for an
artefact-persistence failure *after* the official test had passed was reported
as "not a success" by `repair_success.count` and
`tasks_with_at_least_one_success`, yet still contributed a passing repetition
to `mean_task_pass_rate` / `per_task_pass_rate`.

**Fix (one metric, one condition):** `benchmarks/benchmark/analysis.py` —
`task_pass_rates` now counts a repetition as a pass only when
`r.status == AttemptStatus.REPAIR_SUCCESS`. The per-task denominator
(`len(task_records)`), the `scheduled` / `attempted` /
`candidate_produced` denominators, the `test_pass` metric, the retry
behaviour and every other metric are unchanged.

**Verification:**

| Check | Result |
|---|---|
| Demoted attempt after a *passing* official test | `repair_success.count == 0`, `tasks_with_at_least_one_success == 0`, `mean_task_pass_rate == 0.0`, `per_task_pass_rate == {'HE-EDIT-006': 0.0}` |
| Denominators after the fix | `scheduled_attempts == 1`, `recorded_attempts == 1`, `planned_attempts == 1` — the attempt is still counted, never dropped |
| Effect on a normal 300-attempt mock run | every counting metric and all three paired comparisons are **identical** before and after the fix; only the measured wall-clock latencies differ (as they do between any two runs) |
| Focused artefact tests (network blocked) | **34 passed** (was 33) — one new regression test, `test_a_demoted_attempt_is_not_counted_as_a_task_pass` |
| Full suite (network blocked) | **338 passed, 23 subtests passed** (was 337) |
| Phase 0 manifest | `tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)`, exit 0 |

**Files changed by this final fix:** `benchmarks/benchmark/analysis.py`,
`benchmarks/tests/test_phase3_artifacts.py` and this report. Nothing else.

---

## 11. Final review — independent re-verification and three small corrections

**Review date:** 2026-10-21
**Repository:** `/Users/noshinebnatadrita/Documents/499B`
**Review type:** independent, read-only source inspection plus offline
execution with the network blocked.
**Live provider requests made during this review:** **0**.
**Live pilot started:** **no**. **300-attempt experiment started:** **no**.

### 11.1 Scope and method of this review

The repository is **not a Git repository** (`fatal: not a git repository`), so
no commit hash, branch or diff is available and "files changed since the
previous audit" was reconstructed from filesystem modification times rather
than from version control. Git was not initialised and no Git command other
than the initial existence probe was run.

The Phase 3.1 remediation wave is unambiguously identified by its mtimes
(2026-10-21 15:46–16:30): `provider.py`, `artifacts.py`, `pipeline.py`,
`records.py`, `experiment.py`, `analysis.py`, `test_phase3_artifacts.py`,
`docs/PHASE1_BENCHMARK_DESIGN.md` and this report. Everything else in the
repository predates it.

This review re-verified the persistence, provenance, redaction, failure
handling and metric behaviour from source, closed three small defects it
found, and re-ran the offline evidence.

### 11.2 Files changed during this review

| File | Change | Why |
|---|---|---|
| `benchmarks/benchmark/provider.py` | The `_SECRET_RULES` tuple was extended with narrowly-scoped rules: non-Bearer `Authorization` schemes (`Basic`, `Digest`, `Negotiate`, `Token`), credential-named assignments (`password=`, `passwd=`, `secret=`, `client_secret=`), AWS-style `AKIA…` identifiers, JWT-like `eyJ…` strings, Slack-like `xoxb-…` tokens and URL basic authentication. The rule set stays the single source of truth for both `contains_credential()` and `redact_secrets()`. | §3 of the brief listed credential shapes the first remediation did not cover. No change to `LiveProvider`, the dispatcher call, the model configuration, the evaluation, or any metric. |
| `benchmarks/benchmark/artifacts.py` | New module-level `resolve_artifact_root(output_dir, run_id)`; `ArtifactStore._resolve_root` now delegates to it. The write, redaction and provenance logic is unchanged. | The artefact root was decided in two places that could disagree. |
| `benchmarks/benchmark/experiment.py` | `_resolve_output_paths` now reports `artifacts_dir` via `resolve_artifact_root(...)` instead of the hard-coded `<output>/artifacts`. | **Defect (new finding).** On a re-used output directory the store uses `artifacts-<run_id>`, but `run_metadata.json` and `summary.json` reported the stale plain `artifacts` path. Metadata pointer only — no behaviour, metric or file-content change. |
| `benchmarks/tests/test_phase3_artifacts.py` | A stale comment that claimed the candidate is stored verbatim was corrected. New regression tests: six named-secret assignment cases, three non-Bearer `Authorization` cases, AWS `AKIA` (including the word-adjacent form), JWT, Slack, URL basic authentication, fifteen benign-text/ordinary-code cases, a 20-task corpus scan, a structured-metadata scrubbing case, an end-to-end "no new-shape secret reaches any artefact" case, and two artefact-root metadata cases (fresh directory and re-used directory). | Coverage for the new rules and for the `artifacts_dir` defect. |
| `docs/PHASE1_METRICS_AND_ANALYSIS.md` | §2.1, §2.2 and §3 now define `repair_success` and the task-level quantities by `status == AttemptStatus.REPAIR_SUCCESS`, matching `benchmarks/benchmark/analysis.py`. | **Factual inconsistency.** The document still defined `repair_success.count` as `|{a : a.test_passed is True}|`, which contradicts the implemented status-based rule in exactly the demotion case. |
| `docs/PHASE1_BENCHMARK_DESIGN.md` | The §8.1 redaction-policy paragraph now lists the additionally covered credential shapes and states that detection is heuristic. | The list was incomplete after the rule extension. |
| `docs/PHASE3_1_ARTIFACT_AUDIT.md` | §10.2's test attribution was corrected (see §11.3) and this §11 was added. | Accuracy of the record. |

**Not changed:** the 20-task manifest and every task file, the official
evaluator (`benchmarks/phase0/runner.py`), the evaluation criteria, the
three method implementations and their adapters, `surgical_splicer.py`,
`sandbox.py`, `skeletonizer.py`, `model_dispatcher.py`, the retry policy, the
token/cost semantics, the requested-versus-served model semantics, the
provider request behaviour, the existing Phase 0 outputs, and every file
under `results/`.

### 11.3 Correction to an earlier attribution in this report

§10.2 previously credited the write-stage 1/2/3 failure test with the
assertions `mean_task_pass_rate == 0.0` and `per_task_pass_rate == 0.0`, and
with the "no `.tmp` file left behind" and "partial directory referenced by
the record" observations. Those assertions are made by
`test_a_demoted_attempt_is_not_counted_as_a_task_pass`, not by
`test_failures_at_different_write_stages_are_surfaced`. The row has been
split so that each claim is attributed to the test that actually makes it.
The *values* were and are correct; only the attribution was wrong.

### 11.4 Test commands and observed results

Network access was blocked by the repository's established offline guard
(`/tmp/offline_guard/sitecustomize.py`, inspected before use: it replaces
`socket.socket`, `socket.create_connection` and `socket.socketpair` with
raising stubs, and is activated by `PYTHONPATH` because `sitecustomize` is
imported by the `site` module at interpreter start). The benchmark test
package additionally installs its own session-scoped socket block
(`benchmarks/tests/conftest.py:31-46`).

| # | Command | Observed result | Exit code |
|---|---|---|---|
| 1 | `PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest benchmarks/tests/test_phase3_artifacts.py -q` | **67 passed** in 2.07 s (was 34) | **0** |
| 2 | `PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q` | **371 passed, 23 subtests passed** in 35.29 s (was 338) | **0** |
| 3 | `.venv/bin/python benchmarks/phase0/validate_tasks.py --verify-manifest` | `tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)` | **0** |

The previous baseline (34 focused / 338 full / manifest OK) was reproduced
and then extended by the 33 new regression tests.

### 11.5 Candidate provenance and hash linkage — verified

Re-read from source; the chain is unchanged by this review.

*   `pipeline.py:202` evaluates `adapter_result.candidate_source`;
    `pipeline.py:230` records the digest of that same string in
    `evaluation.candidate_sha256`.
*   `artifacts.py:203-219` computes `candidate_sha256` over the **evaluated**
    bytes, writes either those bytes or a redacted copy to `candidate.py`,
    and records `candidate_file_sha256` over the **stored** bytes.
*   Normal path: `candidate_sha256 == candidate_file_sha256 == sha256(candidate.py) == attempt.json candidate_sha256 == attempt.json evaluation.candidate_sha256`,
    and `candidate_redacted` is `false`.
*   Redacted path: `candidate_sha256 != candidate_file_sha256`, both digests
    are in the record, the CSV and `attempt.json`, and `candidate_redacted`
    is `true`. The stored bytes are the redacted representation.
*   Paths are confined to the run's artefact root
    (`artifacts.py:100-101` computes them relative to the output directory),
    each attempt id maps to exactly one directory, and an existing attempt
    directory is refused (`artifacts.py:144-149`).
*   The new regression test `test_a_reused_output_directory_reports_the_run_scoped_root`
    confirms that two runs in one directory use **different** roots
    (`artifacts` and `artifacts-<run_id>`), that both reported roots contain
    their own attempt directory, that each record's `artifacts_path` agrees
    with the reported root, and that the first run's files are untouched.

### 11.6 Redaction coverage and residual risks

Verified with a probe executed against the **shipped** implementation (not
a copy of the rules): **37 synthetic cases, 0 failures, 0 detection/redaction
parity failures.**

*   Detected **and** redacted (24): `password=`, `passwd=`, `secret=`,
    `client_secret=`, quoted and unquoted, with `=` and `:`; `Authorization:
    Basic`, `authorization=Basic`, `Authorization: Token`; `AKIA…` bare and
    word-adjacent; JWT-like `eyJ…`; Slack-like `xoxb-…`; URL basic
    authentication `https://user:…@host/path`; and the previously covered
    `sk-…`, `or-…`, `Bearer …`, `x-api-key:`, `api_key=`, `token=` forms.
*   Left untouched (15): `task-oriented approach`, `the risk-oriented team`,
    `sk-1`, `password = os.getenv("PW")`, `password = getpass("x")`,
    `secret = None`, `token = 1234`, `the secret sauce`, `secret: 42`,
    `password = my_password`, `https://example.invalid/path`,
    `http://localhost:3000/@user`, `git+ssh://git@github.com`,
    `authorization = "none"`.
*   **The benchmark corpus is unaffected:** 89 files under `benchmarks/data`
    (80 task files across the 20 tasks, plus the model-facing payload and the
    manifest) were scanned; the detector fired on **none** of them and the
    redaction altered **none** of them. The same check is now a regression
    test.
*   End-to-end: a synthetic secret of a new shape placed in the response and
    in the candidate does not appear in any persisted file (candidate, raw
    response, `attempt.json`, JSONL, CSV, summary); the attempt is flagged
    `candidate_redacted = true` and the two digests remain distinguishable.
*   Structured metadata: `_scrub_mapping` drops credential-like **keys** and
    redacts values, including the new shapes (regression test added).
*   The stderr tail is still redacted **before** it is truncated
    (`pipeline.py:275`), so a truncation cannot leave an unmatchable fragment.

**Residual risks (explicitly not closed):**

1.   Detection is **heuristic**. It recognises credential *shapes*, not
    credentials. A credential in an unrecognised format — for example a bare
    hex token, a provider-specific prefix that is not `sk-`/`or-`, or a
    secret with no key word in front of it — will **not** be detected.
2.   The named-secret rules are deliberately conservative: a value is only
    redacted when it carries a digit or is at least twelve characters long.
    A short alphabetic secret such as `password=hunter2` is therefore
    **not** redacted. This trade-off was made to avoid corrupting ordinary
    source code such as `password = os.getenv("PW")`.
3.   A redacted candidate is **not** byte-identical to the evaluated bytes.
    The two digests make the difference explicit; no byte-equality is
    claimed in that branch.
4.   The offline guard blocks Python-level sockets in the test process. It
    does not constrain a child process or a non-Python client.

### 11.7 Atomic persistence and failure handling — verified

*   Writes go through a temporary file in the same directory, `flush`,
    `os.fsync`, then `os.replace` (`artifacts.py:103-126`). On an `OSError`
    the temporary file is removed and `ArtifactPersistenceError` is raised.
*   An existing attempt directory is refused outright; the store never
    overwrites an existing artefact.
*   A persistence failure demotes the attempt to `incomplete` /
    `internal_error` with `artifacts_status="failed"`
    (`experiment.py:244-274`), sets `artifacts_path` to the (possibly
    partial) directory so it stays discoverable, prints the error to stderr,
    and still appends the record to JSONL and CSV — the attempt therefore
    stays in the scheduled and recorded denominators.
*   Fault injection at write stages 1, 2 and 3 confirms the demotion, the
    JSONL↔CSV consistency, and that no `.tmp` file survives. The failure is
    never reported as a successful repair.
*   Error messages and persisted diagnostics are redacted before they are
    stored; no credential was printed by any test.

### 11.8 Task-level metric regression — verified

The demotion case (official test passes, artefact persistence then fails)
was reproduced end to end:

| Quantity | Observed |
|---|---|
| `record.test_passed` | `True` |
| `record.status` | `incomplete` |
| `record.artifacts_status` | `failed` |
| `repair_success.count` | `0` |
| `tasks_with_at_least_one_success` | `0` |
| `mean_task_pass_rate` | `0.0` |
| `per_task_pass_rate["HE-EDIT-006"]` | `0.0` |
| `scheduled_attempts` / `recorded_attempts` / `planned_attempts` | `1` / `1` / `1` (unchanged) |

`analysis.py:167` counts a repetition as a pass only when
`r.status == AttemptStatus.REPAIR_SUCCESS`, exactly as `solved_tasks`
(`analysis.py:157-161`), `repair_success.count` (`analysis.py:123`) and the
paired comparison (`analysis.py:325`) do. The per-task denominator
(`len(task_records)`, `analysis.py:169`) is unchanged, and no other metric
definition was touched.

**Effect on a normal run (mock data only).** An isolated mock run
(2 tasks × 3 methods × 3 repetitions = 18 attempts, a fake provider with
`is_mock=True`, in a fresh temporary directory outside the repository) was
used to compare the fixed metric against the pre-fix definition on the same
records:

*   `per_task_pass_rate` is **identical** under both definitions for all
    three methods (the run contained 3 `repair_success`, 9 `repair_failed`
    and 6 `output_parse_error` attempts, so the successes were genuinely
    exercised, not trivially zero);
*   every denominator, the syntax-valid and test-pass counts and all three
    paired comparisons were unaffected.

The run is **mock data**: `mode="mock"`, `is_measurement=false`,
`contains_mock_results=true`, a `mock_warning` in the summary, `is_mock=true`
on every record, **0** provider-reported usage attempts and **0**
provider-reported cost attempts, and no token or cost value was fabricated.
It is a pipeline test, not a measurement of model quality.

### 11.9 Scope confirmation

The following were **not** changed by this review: the 20-task manifest and
all task files; the official evaluator and the evaluation criteria; the
three method implementations and their adapters; the retry behaviour; the
provider request behaviour; the requested-versus-served model metadata
semantics; the token/cost measurement semantics; the existing Phase 0
outputs; and every file under `results/`.

A SHA-256 baseline of 105 protected files (all task files, the Phase 0
package, the adapters, `tasks.py`, `surgical_splicer.py`, `sandbox.py`,
`skeletonizer.py`, `model_dispatcher.py`) was taken before the first edit
and re-verified afterwards: **all 105 digests are identical**. The mtimes of
every file under `results/` and of `.env` are unchanged.

### 11.10 Verified facts, limitations and recommendations

**Verified facts**

*   The candidate bytes submitted to the official evaluator are preserved
    and bound to their digest; the redaction branch is represented by two
    distinct digests and a flag.
*   Artefact writes are atomic; a persistence failure demotes the attempt
    and can never be reported as a success; the attempt stays in the
    denominators.
*   The task-level metrics, `solved_tasks` and `repair_success` share one
    success definition, and that definition is unchanged for normal runs.
*   The redaction detector and the redactor share one rule set, cover 37
    verified synthetic shapes, and leave the benchmark corpus untouched.
*   The offline evidence is green: 67 focused tests, 371 full-suite tests
    plus 23 subtests, and a 20/20 Phase 0 manifest — all exit 0.

**Limitations that remain**

1.   The execution sandbox is **best-effort isolation, not a security
    boundary**; persisting artefacts does not change that.
2.   Credential detection is **heuristic** and may miss unrecognised
    formats; short alphabetic secrets are not covered (§11.6).
3.   A redacted candidate's stored bytes differ from the evaluated bytes;
    both digests are retained so the difference stays visible.
4.   The earlier alias-based pilot used inconsistent served models
    (`openrouter/free` served two different models across three attempts,
    confirmed from `results/pilot_.../summary.json` →
    `observed_served_model_ids`) and is **not valid** for strong method
    comparisons.
5.   The previous pilot's candidate artefacts were **never persisted** and
    **cannot be recovered** from the missing files.
6.   There is **no size cap or retention policy** on persisted responses and
    candidates; a 300-attempt run would store 900 files.
7.   The offline guard constrains the test process, not child processes.

**Recommendation before any live work**

A live pilot requires **separate, explicit user approval** and a
**reviewed, pinned, non-alias model identifier** (the dynamic aliases are
refused by default in `provider.py:336`). Passing the offline suite does
**not** constitute approval for live execution, and this review does not
grant it.
