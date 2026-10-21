# Phase 1 Metrics and Analysis

This document defines every metric the runner computes, the denominator it
uses, and the rules that keep mock output and provider errors from being
mistaken for repair quality.

**No live results exist yet.** The only summaries produced so far come from
mock-mode pipeline tests and are labelled as such.

---

## 1. Denominators

Different questions have different denominators. Every metric below names the
denominator it uses, and the denominators are reported together in the
`denominators` block of each method's metrics.

| Denominator | Definition | Used by |
|---|---|---|
| `scheduled_attempts` | every attempt in the schedule for that method: `tasks × reps` (= 100 per method for the full experiment) | repair success rate over the schedule, malformed-output rate, provider-failure rate, timeout rate |
| `recorded_attempts` | attempts that produced a record (every attempt does) | completeness checks |
| `attempted_attempts` | attempts that produced a provider response (i.e. excluding provider errors) | repair success rate conditioned on a response |
| `candidate_produced_attempts` | attempts whose response was parsed and applied to a candidate module | syntax-valid rate, test-pass rate |
| `tasks` | distinct tasks (20) | task-level success rate |
| provider-reported subset | attempts for which the provider actually returned usage/cost | token and cost aggregates |

A failed, malformed, timed-out or provider-error attempt is **never** removed
from a denominator. Nothing is dropped from the schedule.

---

## 2. Definitions

Let `A` be the attempts of one method.

### 2.1 Repair success

```
repair_success.count        = |{a in A : a.status == repair_success}|
repair_success.rate_over_scheduled    = count / scheduled_attempts
repair_success.rate_over_attempted    = count / attempted_attempts
```

`repair_success` is a **status** definition, not a `test_passed` definition.
An attempt reaches `status == repair_success` only when the *candidate*
module produced by the method passed the task's official `check(candidate)`
test inside the sandbox **and** the attempt was not demoted afterwards. An
attempt that was demoted — for example because its artefacts could not be
persisted — is never counted as a repair success, even though
`test_passed` is still `True` for it. `test_passed` remains the raw
evaluation outcome and is reported separately in §2.3.

### 2.2 Task-level success

A task counts as solved by a method when **at least one** of its repetitions
completed as a repair success (`status == repair_success`).

```
task_level.tasks_with_at_least_one_success
task_level.task_success_rate      = tasks_with_at_least_one_success / tasks
task_level.mean_task_pass_rate    = mean over tasks of (successful reps / reps of that task)
```

The same success definition is used for `solved_tasks`,
`repair_success.count`, `mean_task_pass_rate`, `per_task_pass_rate` and the
paired comparison in §3, so those quantities can never disagree with each
other. A repetition that was demoted after the official test had already
passed therefore contributes **0** to its task's numerator while still
counting in the task's denominator: the attempt is never silently dropped.

`mean_task_pass_rate` is the primary comparison quantity because it uses all
five repetitions of every task without treating repetitions as independent
tasks.

### 2.3 Syntax validity and test pass (candidate-level)

```
syntax_valid.rate_over_candidates = |{a : a.syntax_valid is True}| / candidate_produced_attempts
test_pass.rate_over_candidates     = |{a : a.test_passed is True}| / candidate_produced_attempts
```

A malformed output cannot be syntactically valid, so it is excluded here and
counted in §2.4 instead.

The candidate set used by both rates contains the attempts whose status is
`repair_success` or `repair_failed`. An attempt that was **demoted** after
the evaluation (for example because its artefacts could not be persisted) is
no longer a candidate attempt: it is excluded from both the numerator and the
`candidate_produced_attempts` denominator here, and it is not counted as a
success in §2.1/§2.2 either. Its raw evaluation outcome remains visible in
the per-attempt record (`test_passed`, `test_error_type`). The attempt is
never dropped from the `scheduled`, `recorded` or `attempted` denominators.

### 2.4 Malformed output, provider failure, timeouts

```
malformed_output.rate_over_scheduled = |{a : a.status == output_parse_error}| / scheduled_attempts
provider_failures.rate_over_scheduled = |{a : a.status == provider_error}| / scheduled_attempts
timeouts.rate_over_scheduled          = |{a : a.timed_out}| / scheduled_attempts
```

### 2.5 Normalised failure categories

`failure_category` is one of:

`none`, `syntax_error`, `test_failed`, `output_parse_error`,
`truncated_output`, `timeout`, `auth_error`, `quota_exceeded`,
`provider_error`, `network_error`, `configuration_error`,
`invalid_response`, `internal_error`.

`truncated_output` is used when the provider reported
`finish_reason == "length"` (the generation hit the output limit) **and** the
truncated response could not be applied. It is deliberately distinct from
`output_parse_error`, which covers a response that was complete but did not
follow the method's output contract.

Provider categories (`auth_error`, `quota_exceeded`, `timeout`,
`network_error`, `provider_error`) describe an **infrastructure** failure: no
repair was attempted, so they are reported separately from the repair-quality
categories and are excluded from the candidate-level denominators.

### 2.6 Latency

Measured locally, per attempt, around the whole attempt (provider call +
parse + evaluation):

```
latency_ms.mean / median / min / max
provider_latency_ms.*   (the provider-reported wall-clock inside the client)
```

There are no hardcoded latency constants in the Phase 1 code. The legacy
`edit_orchestrator.py` contains hardcoded 1650/520/185 ms values; that file is
not used by this benchmark.

### 2.7 Tokens and cost

```
token_usage.provider_reported_attempts   = attempts where the provider reported a complete token set
token_usage.provider_partial_attempts    = attempts where the provider reported only some token fields
token_usage.local_estimate_only_attempts = attempts where only a local estimate exists
token_usage.missing_attempts             = attempts where the provider reported no token value at all
token_usage.total_tokens_total           = sum of provider-reported total tokens
cost.provider_reported_attempts          = attempts whose provider reported a cost
cost.total_cost_usd                      = sum of provider-reported costs, or null
```

*   **Provenance is tracked per field.** A token count is labelled
    provider-reported only when the provider actually supplied that field. A
    locally estimated count is stored in a *separate* field
    (`estimated_prompt_tokens` / `estimated_completion_tokens`) and is never
    added to a provider-reported total.
*   `usage_source` on each attempt is one of:
    `provider_reported` (prompt **and** completion reported),
    `provider_partial` (some fields reported),
    `local_estimate` (nothing reported),
    `local_estimate_not_reported` (the mock engine, which reports nothing).
*   A missing value is stored as `null`, never as `0`.
*   The mock engine's token counts are **local estimates**, so they are stored
    as `null` with `usage_source="local_estimate_not_reported"`. They are
    never presented as provider-reported usage.
*   `cost_usd` is filled only when the provider response itself contains a
    cost. If a price table is added later, it must be stored in separate
    `*_estimate` fields and labelled as an estimate.
*   `cost_source` records where the number came from (`provider_reported` or
    `null`).

### 2.8 Mock contamination

```
contains_mock_results = any(a.is_mock)
is_measurement        = not contains_mock_results
```

A summary with `contains_mock_results == true` is a **pipeline test**, not a
measurement. The runner writes `mode`, `is_measurement` and `mock_warning`
into `summary.json`, and every record carries `is_mock`.

---

## 3. Comparison between two methods (paired, at the task level)

For each task `t` and method `m`:

```
r_m(t) = successful repetitions of m on t / repetitions of m on t
```

A repetition is *successful* when its status is `repair_success` (§2.1), so
the paired comparison uses exactly the same success definition as the
task-level metrics above.

The comparison of method A against method B uses the shared tasks only:

```
difference(t) = r_A(t) - r_B(t)
mean_difference_a_minus_b = mean over shared tasks of difference(t)
bootstrap_95ci            = percentile bootstrap over tasks (2000 resamples,
                             seed 20260101) of the mean difference
task_wins_a / task_losses_a / task_ties
```

The bootstrap resamples **tasks**, not attempts, because tasks are the
independent unit. With 20 tasks the interval is wide and no strong
statistical-significance claim is made; the interval is reported so that the
reader can see how uncertain the difference is.

---

## 4. Reporting rules

*   Never report a HumanEval pass rate. The task set is synthetic and
    HumanEval-derived.
*   Never present a mock summary as a research finding.
*   Always state the denominators next to a rate.
*   Keep provider errors visible; do not silently exclude them from the
    scheduled denominator.
*   Report missing data (missing usage, missing cost, missing served model id)
    explicitly rather than substituting zero.

---

## 5. Worked example (mock mode, pipeline test only)

The mock engine always returns a response that leaves the defect in place, so
a mock run can only ever produce `repair_failed` / `output_parse_error`
outcomes. It demonstrates that the pipeline executes, persists results
incrementally and computes metrics; it says nothing about the quality of any
editing method.

```
mode                     : mock
is_measurement           : false
mock_warning             : MOCK RUN — pipeline test only, not a measurement of model quality.
planned_attempts         : 300
recorded_attempts        : 300
attempts_with_provider_reported_usage : 0
attempts_with_provider_reported_cost  : 0
```
