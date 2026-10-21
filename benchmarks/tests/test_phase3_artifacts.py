"""
Phase 3.1 artefact-persistence tests.

Covers the eight required cases: successful candidate, failing candidate,
parse failure (no fabricated candidate), truncated response, JSONL/CSV
consistency, no overwriting of previous results, no credential leakage, and
surfaced persistence failures.

Everything runs offline with fake providers; no provider is contacted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.artifacts import ArtifactPersistenceError, ArtifactStore  # noqa: E402
from benchmarks.benchmark.experiment import ExperimentRunner  # noqa: E402
from benchmarks.benchmark.pipeline import AttemptPipeline  # noqa: E402
from benchmarks.benchmark.provider import (  # noqa: E402
    BenchmarkProvider,
    MockProvider,
    ProviderResult,
)
from benchmarks.benchmark.records import AttemptStatus, FailureCategory  # noqa: E402

CORRECT_BELOW_ZERO = '''from typing import List


def below_zero(operations: List[int]) -> bool:
    """ You're given a list of deposit and withdrawal operations on a bank account.
    >>> below_zero([1, 2, 3])
    False
    >>> below_zero([1, -2, 3])
    True
    """
    balance = 0
    for op in operations:
        balance += op
        if balance < 0:
            return True

    return False
'''

WRONG_BELOW_ZERO = CORRECT_BELOW_ZERO.replace("balance < 0", "balance <= 0")

FAKE_CREDENTIAL = "sk-live-abcdef0123456789"


class ScriptedProvider(BenchmarkProvider):
    """A fake provider that returns a fixed response."""

    name = "scripted-fake"

    def __init__(self, content: str, finish_reason: str = "stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.model_id = "fake/model"

    def complete(self, prompt, system_instruction=None):
        return ProviderResult(
            content=self.content,
            provider=self.name,
            requested_model_id=self.model_id,
            is_mock=False,
            finish_reason=self.finish_reason,
            served_model_id="fake/model-served",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            usage_source="provider_reported",
        )


def _run(repository, provider, output_dir, **kwargs):
    runner = ExperimentRunner(
        repository=repository,
        provider=provider,
        mode="live" if not isinstance(provider, MockProvider) else "mock",
        output_dir=str(output_dir),
        methods=["full_file_regeneration"],
        reps=1,
        task_ids=["HE-EDIT-006"],
        seed=1,
        **kwargs,
    )
    summary = runner.run(progress=False)
    return runner, summary


def _records(jsonl_path):
    """Return the attempt records as attribute-accessible objects."""
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        return [SimpleNamespace(**json.loads(line)) for line in fh if line.strip()]


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class TestSuccessfulCandidateIsPersisted:

    def test_candidate_file_exists_and_digest_matches_the_saved_bytes(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert record.status == AttemptStatus.REPAIR_SUCCESS
        assert record.test_passed is True
        assert record.candidate_state == "produced"
        assert record.candidate_path
        assert record.artifacts_status == "ok"

        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        assert os.path.exists(candidate_path)
        assert _sha256_file(candidate_path) == record.candidate_sha256

        attempt_json = json.load(
            open(os.path.join(tmp_path / "run", record.artifacts_path, "attempt.json"), encoding="utf-8")
        )
        assert attempt_json["candidate_sha256"] == record.candidate_sha256
        assert attempt_json["evaluation"]["candidate_sha256"] == record.candidate_sha256
        assert attempt_json["evaluation"]["passed"] is True
        assert attempt_json["evaluation"]["attempted"] is True

    def test_raw_response_is_persisted_for_the_attempt(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]
        raw_path = os.path.join(tmp_path / "run", record.raw_response_path)
        assert os.path.exists(raw_path)
        payload = json.load(open(raw_path, encoding="utf-8"))
        assert payload["attempt_id"] == record.attempt_id
        assert payload["task_id"] == "HE-EDIT-006"
        assert payload["method"] == "full_file_regeneration"
        assert payload["requested_model_id"] == "fake/model"
        assert payload["served_model_id"] == "fake/model-served"
        assert "below_zero" in payload["response_text"]


class TestFailingCandidateIsStillPersisted:

    def test_candidate_that_fails_the_official_test_is_kept(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{WRONG_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert record.status == AttemptStatus.REPAIR_FAILED
        assert record.test_passed is False
        assert record.candidate_state == "produced"
        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        assert os.path.exists(candidate_path)
        assert _sha256_file(candidate_path) == record.candidate_sha256

        attempt_json = json.load(
            open(os.path.join(tmp_path / "run", record.artifacts_path, "attempt.json"), encoding="utf-8")
        )
        assert attempt_json["evaluation"]["attempted"] is True
        assert attempt_json["evaluation"]["passed"] is False
        assert attempt_json["evaluation"]["candidate_sha256"] == record.candidate_sha256

    def test_a_failing_candidate_is_distinguishable_from_no_candidate(self, repository, tmp_path):
        failing = ScriptedProvider(f"```python\n{WRONG_BELOW_ZERO}```")
        failing_runner, _ = _run(repository, failing, tmp_path / "failing")
        parsing = ScriptedProvider("no code block here")
        parsing_runner, _ = _run(repository, parsing, tmp_path / "parsing")

        failed_record = _records(failing_runner.output_paths()["attempts_jsonl"])[0]
        parse_record = _records(parsing_runner.output_paths()["attempts_jsonl"])[0]

        assert failed_record.candidate_state == "produced"
        assert failed_record.test_passed is False
        assert parse_record.candidate_state == "none"
        assert parse_record.candidate_path is None
        assert parse_record.test_passed is None


class TestParseFailureCreatesNoCandidate:

    def test_no_candidate_file_is_fabricated(self, repository, tmp_path):
        provider = ScriptedProvider("I cannot follow the format.")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert record.status == AttemptStatus.OUTPUT_PARSE_ERROR
        assert record.candidate_state == "none"
        assert record.candidate_path is None
        assert record.candidate_sha256 is None
        attempt_dir = os.path.join(tmp_path / "run", record.artifacts_path)
        assert os.path.exists(os.path.join(attempt_dir, "attempt.json"))
        assert os.path.exists(os.path.join(attempt_dir, "raw_response.json"))
        assert not os.path.exists(os.path.join(attempt_dir, "candidate.py"))

    def test_truncated_response_is_classified_and_auditable(self, repository, tmp_path):
        provider = ScriptedProvider("```python\ndef below_zero(ops", finish_reason="length")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert record.failure_category == FailureCategory.TRUNCATED_OUTPUT
        assert record.truncated_output is True
        assert record.finish_reason == "length"
        # The audit information is retained.
        raw_path = os.path.join(tmp_path / "run", record.raw_response_path)
        assert os.path.exists(raw_path)
        payload = json.load(open(raw_path, encoding="utf-8"))
        assert payload["finish_reason"] == "length"
        assert payload["response_text"].startswith("```python")
        attempt_json = json.load(
            open(os.path.join(tmp_path / "run", record.artifacts_path, "attempt.json"), encoding="utf-8")
        )
        assert attempt_json["finish_reason"] == "length"


class TestRecordConsistency:

    def test_jsonl_csv_and_artifacts_agree(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        records = _records(runner.output_paths()["attempts_jsonl"])
        with open(runner.output_paths()["attempts_csv"], "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        assert len(records) == len(rows) == 1
        record, row = records[0], rows[0]
        for key in (
            "candidate_path",
            "candidate_sha256",
            "raw_response_path",
            "artifacts_path",
            "artifacts_status",
            "candidate_state",
        ):
            assert str(getattr(record, key)) == row[key], key

        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        assert os.path.exists(candidate_path)
        assert _sha256_file(candidate_path) == record.candidate_sha256

    def test_summary_reports_the_artifact_directories(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        assert summary["artifacts_persisted"] == 1
        assert summary["artifacts_failed"] == 0
        assert summary["candidates_persisted"] == 1
        assert summary["artifacts_dir"]


class TestNoOverwriting:

    def test_a_second_run_does_not_overwrite_the_first(self, repository, tmp_path):
        first_provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        first, _ = _run(repository, first_provider, tmp_path / "run", allow_overwrite=True)
        first_records = _records(first.output_paths()["attempts_jsonl"])
        first_candidate = os.path.join(tmp_path / "run", first_records[0].candidate_path)
        first_bytes = open(first_candidate, "rb").read()
        first_attempt_json = os.path.join(
            tmp_path / "run", first_records[0].artifacts_path, "attempt.json"
        )
        first_attempt_bytes = open(first_attempt_json, "rb").read()

        second_provider = ScriptedProvider(f"```python\n{WRONG_BELOW_ZERO}```")
        second, _ = _run(repository, second_provider, tmp_path / "run", allow_overwrite=True)
        second_records = _records(second.output_paths()["attempts_jsonl"])
        second_candidate = os.path.join(tmp_path / "run", second_records[0].candidate_path)

        # The first run's artefacts are untouched.
        assert open(first_candidate, "rb").read() == first_bytes
        assert open(first_attempt_json, "rb").read() == first_attempt_bytes
        assert first_records[0].artifacts_path != second_records[0].artifacts_path
        assert first_candidate != second_candidate
        # The two runs use different file sets as well.
        assert first.output_paths()["attempts_jsonl"] != second.output_paths()["attempts_jsonl"]
        # The second run has its own record file; the first one is intact.
        assert os.path.exists(first.output_paths()["attempts_jsonl"])
        assert len(_records(first.output_paths()["attempts_jsonl"])) == 1
        assert len(second_records) == 1
        assert second_records[0].attempt_id != first_records[0].attempt_id

    def test_the_store_refuses_to_overwrite_an_existing_attempt_directory(self, repository, tmp_path):
        from benchmarks.benchmark.pipeline import AttemptPipeline
        from benchmarks.benchmark.adapters import get_adapter

        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        pipeline = AttemptPipeline(
            provider=provider,
            adapter=get_adapter("full_file_regeneration"),
            mode="live",
            run_id="run-1",
        )
        outcome = pipeline.run(repository.get("HE-EDIT-006"), 1)
        store = ArtifactStore(str(tmp_path / "run"), "run-1")
        store.persist(outcome)
        with pytest.raises(ArtifactPersistenceError):
            store.persist(outcome)


class TestNoCredentialLeakage:

    def test_credentials_are_not_written_to_the_artefacts(self, repository, tmp_path):
        leaky = (
            "```python\n"
            "# I was told to use Bearer "
            + FAKE_CREDENTIAL
            + "\n"
            "def below_zero(operations):\n    return False\n```"
        )
        provider = ScriptedProvider(leaky)
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        blob = ""
        for root, _dirs, files in os.walk(tmp_path / "run"):
            for name in files:
                with open(os.path.join(root, name), "r", encoding="utf-8", errors="replace") as fh:
                    blob += fh.read()

        assert FAKE_CREDENTIAL not in blob
        assert not re.search(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}", blob)
        assert not re.search(r"(?i)\bsk-[A-Za-z0-9._\-]{6,}", blob)

        # The candidate was redacted before it was stored (storing the raw
        # bytes would leak the credential), and the flag makes the redaction
        # visible to a reviewer.
        assert record.candidate_redacted is True
        attempt_json = json.load(
            open(os.path.join(tmp_path / "run", record.artifacts_path, "attempt.json"), encoding="utf-8")
        )
        assert attempt_json["candidate_redacted"] is True

    def test_authorization_like_keys_are_dropped_from_metadata(self, tmp_path):
        store = ArtifactStore(str(tmp_path / "run"), "run-1")
        assert store is not None
        from benchmarks.benchmark.artifacts import _scrub_mapping

        scrubbed = _scrub_mapping(
            {
                "api_key": "sk-should-not-survive",
                "authorization": "Bearer abcdefgh",
                "nested": {"x_api_key": "sk-nope", "safe": "kept"},
                "note": "nothing sensitive",
            }
        )
        assert "api_key" not in scrubbed
        assert "authorization" not in scrubbed
        assert "x_api_key" not in scrubbed["nested"]
        assert scrubbed["nested"]["safe"] == "kept"


class TestPersistenceFailuresAreSurfaced:

    def test_a_failed_artifact_write_cannot_be_reported_as_success(self, repository, tmp_path, monkeypatch):
        from benchmarks.benchmark import artifacts as artifacts_module

        def broken_write(self, path, data):
            raise artifacts_module.ArtifactPersistenceError(
                f"simulated disk failure for {os.path.basename(path)}"
            )

        monkeypatch.setattr(artifacts_module.ArtifactStore, "_write", broken_write)

        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        # The attempt that would have been a success is NOT a success.
        assert record.status == AttemptStatus.INCOMPLETE
        assert record.failure_category == FailureCategory.INTERNAL_ERROR
        assert record.artifacts_status == "failed"
        assert record.candidate_state == "none"
        assert "artifact persistence failed" in (record.error_message or "")
        assert summary["artifacts_failed"] == 1
        assert summary["artifacts_persisted"] == 0
        # The attempt is still present in the denominator.
        assert summary["recorded_attempts"] == 1
        assert summary["planned_attempts"] == 1

    @pytest.mark.parametrize("failing_write_index", [1, 2, 3])
    def test_failures_at_different_write_stages_are_surfaced(
        self, repository, tmp_path, monkeypatch, failing_write_index
    ):
        """A failure at any stage must demote the attempt and stay consistent."""
        from benchmarks.benchmark import artifacts as artifacts_module

        original = artifacts_module.ArtifactStore._write
        counter = {"n": 0}

        def flaky(self, path, data):
            counter["n"] += 1
            if counter["n"] == failing_write_index:
                raise artifacts_module.ArtifactPersistenceError(
                    f"simulated failure at write #{failing_write_index}"
                )
            return original(self, path, data)

        monkeypatch.setattr(artifacts_module.ArtifactStore, "_write", flaky)

        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert record.status == AttemptStatus.INCOMPLETE
        assert record.failure_category == FailureCategory.INTERNAL_ERROR
        assert record.artifacts_status == "failed"
        assert summary["artifacts_failed"] == 1
        # The attempt is NOT counted as a repair success, even when the
        # official test happened to pass before the persistence failure.
        metrics = summary["method_metrics"]["full_file_regeneration"]
        assert metrics["repair_success"]["count"] == 0
        assert metrics["task_level"]["tasks_with_at_least_one_success"] == 0
        assert metrics["denominators"]["candidate_produced_attempts"] == 0

    def test_a_demoted_attempt_is_not_counted_as_a_task_pass(self, repository, tmp_path, monkeypatch):
        """
        A passing official test whose attempt was demoted for an
        artefact-persistence failure must not be counted as a repair success
        *or* as a passing repetition in the task-level metrics.
        """
        from benchmarks.benchmark import artifacts as artifacts_module

        original = artifacts_module.ArtifactStore._write

        def failing_attempt_manifest(self, path, data):
            if path.endswith("attempt.json"):
                raise artifacts_module.ArtifactPersistenceError(
                    "simulated failure at the attempt.json stage"
                )
            return original(self, path, data)

        monkeypatch.setattr(
            artifacts_module.ArtifactStore, "_write", failing_attempt_manifest
        )

        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        # The attempt really did pass the official test before the demotion.
        assert record.test_passed is True
        assert record.status == AttemptStatus.INCOMPLETE
        assert record.artifacts_status == "failed"

        metrics = summary["method_metrics"]["full_file_regeneration"]
        assert metrics["repair_success"]["count"] == 0
        assert metrics["task_level"]["tasks_with_at_least_one_success"] == 0
        assert metrics["task_level"]["mean_task_pass_rate"] == 0.0
        assert metrics["task_level"]["per_task_pass_rate"]["HE-EDIT-006"] == 0.0
        # The denominators are untouched: the attempt is still counted.
        assert metrics["denominators"]["scheduled_attempts"] == 1
        assert metrics["denominators"]["recorded_attempts"] == 1
        assert summary["recorded_attempts"] == 1
        assert summary["planned_attempts"] == 1

        # JSONL and CSV remain consistent.
        with open(runner.output_paths()["attempts_csv"], "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        for key in ("status", "failure_category", "artifacts_status", "candidate_state", "artifacts_path"):
            assert str(getattr(record, key)) == rows[0][key], key

        # A partially written directory is discoverable through the record
        # instead of being left as an orphan.
        if record.artifacts_path:
            assert os.path.isdir(os.path.join(tmp_path / "run", record.artifacts_path))
        # No half-written file is left behind (writes are atomic).
        for root, _dirs, files in os.walk(tmp_path / "run"):
            for name in files:
                assert not name.endswith(".tmp"), name


class TestRedactionRemediation:
    """Phase 3.1 audit findings M-A and M-B."""

    POSITIONS = {
        "string start": "{s}",
        "after equals": "key={s}",
        "after quote": '"{s}"',
        "after space": "token {s}",
        "word adjacent": "prefix{s}",
        "digit adjacent": "id123{s}",
        "url query": "https://example.invalid/?k={s}",
        "bearer prefix": "Bearer {s}",
    }

    @pytest.mark.parametrize("position", list(POSITIONS))
    def test_synthetic_secret_is_redacted_in_every_position(self, position):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        text = self.POSITIONS[position].format(s=FAKE_CREDENTIAL)
        assert contains_credential(text) is True
        assert FAKE_CREDENTIAL not in redact_secrets(text)

    @pytest.mark.parametrize(
        "text",
        ['api_key="supersecretvalue123456"', 'token="supersecretvalue123456"'],
    )
    def test_key_assignment_forms_are_detected_and_redacted(self, text):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        assert contains_credential(text) is True
        assert "supersecretvalue123456" not in redact_secrets(text)

    def test_benign_prose_is_not_redacted(self):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        for text in ("task-oriented approach", "the risk-oriented team", "sk-1"):
            assert contains_credential(text) is False
            assert redact_secrets(text) == text

    def test_detection_and_redaction_share_one_rule_set(self):
        """The candidate trigger must be the same rule set that redacts."""
        from benchmarks.benchmark import artifacts as artifacts_module
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        # The artefact module no longer carries its own pattern set.
        assert not hasattr(artifacts_module, "_CREDENTIAL_SHAPED")
        samples = [
            FAKE_CREDENTIAL,
            "prefix" + FAKE_CREDENTIAL,
            'api_key="supersecretvalue123456"',
            'token="supersecretvalue123456"',
            "Bearer " + FAKE_CREDENTIAL,
            "nothing to see here",
        ]
        for sample in samples:
            assert contains_credential(sample) == ("[REDACTED]" in redact_secrets(sample))

    def test_no_synthetic_secret_reaches_any_persisted_artefact(self, repository, tmp_path):
        """Response, candidate, stderr, JSONL, CSV and summary must be clean."""
        import re as _re

        leaky_candidate = (
            "def below_zero(operations):\n"
            "    # note " + FAKE_CREDENTIAL + "\n"
            "    return False\n"
        )
        leaky_response = (
            "Here is the repair, using key " + FAKE_CREDENTIAL + "\n"
            "```python\n" + leaky_candidate + "```"
        )
        provider = ScriptedProvider(leaky_response)
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        blob = ""
        for root, _dirs, files in os.walk(tmp_path / "run"):
            for name in files:
                with open(os.path.join(root, name), "r", encoding="utf-8", errors="replace") as fh:
                    blob += fh.read()

        assert FAKE_CREDENTIAL not in blob
        assert not _re.search(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}", blob)
        # The candidate was redacted on disk and the record says so.
        assert record.candidate_redacted is True
        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        assert FAKE_CREDENTIAL not in open(candidate_path, encoding="utf-8").read()
        # The record still identifies the bytes that were evaluated.
        assert record.candidate_sha256 and record.candidate_file_sha256
        assert record.candidate_sha256 != record.candidate_file_sha256


class TestCandidateProvenanceRemediation:

    def test_normal_path_has_equal_digests(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        assert _sha256_file(candidate_path) == record.candidate_sha256
        assert record.candidate_file_sha256 == record.candidate_sha256
        assert record.candidate_redacted is False

    def test_redacted_path_reports_both_digests(self, repository, tmp_path):
        leaky = (
            "```python\ndef below_zero(operations):\n"
            "    # " + FAKE_CREDENTIAL + "\n    return False\n```"
        )
        provider = ScriptedProvider(leaky)
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        candidate_path = os.path.join(tmp_path / "run", record.candidate_path)
        file_digest = _sha256_file(candidate_path)
        assert record.candidate_file_sha256 == file_digest
        assert record.candidate_sha256 != file_digest
        assert record.candidate_redacted is True

        attempt_json = json.load(
            open(os.path.join(tmp_path / "run", record.artifacts_path, "attempt.json"), encoding="utf-8")
        )
        assert attempt_json["candidate_file_sha256"] == file_digest
        assert attempt_json["candidate_sha256"] == record.candidate_sha256
        assert attempt_json["evaluation"]["candidate_sha256"] == record.candidate_sha256

    def test_the_record_exposes_the_file_digest_in_jsonl_and_csv(self, repository, tmp_path):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        with open(runner.output_paths()["attempts_csv"], "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert "candidate_file_sha256" in rows[0]
        record = _records(runner.output_paths()["attempts_jsonl"])[0]
        assert rows[0]["candidate_file_sha256"] == record.candidate_file_sha256


class TestAdditionalCredentialShapes:
    """Credential shapes that the first redaction pass did not cover.

    Every value in this class is a *synthetic* dummy.  No real credential is
    used, and none is printed: the assertions compare the redacted output
    against the dummy string only.
    """

    NAMED_SECRETS = [
        'password="FAKEpass123456"',
        "passwd=FAKEpass123456",
        'secret="FAKEsecret98765"',
        "client_secret=FAKEclient0123456789",
        'password = "FAKEpass123456"',
        "passwd: FAKEpass123456",
    ]

    AUTHORIZATION_SCHEMES = [
        "Authorization: Basic ZmFrZS1wYXNz",
        "authorization=Basic ZmFrZS1wYXNz",
        "Authorization: Token FAKEtoken0123456789",
    ]

    @pytest.mark.parametrize("text", NAMED_SECRETS)
    def test_named_secret_assignments_are_detected_and_redacted(self, text):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "[REDACTED]" in redacted
        # The value that followed the key is gone.
        assert "FAKEpass123456" not in redacted
        assert "FAKEsecret98765" not in redacted
        assert "FAKEclient0123456789" not in redacted

    @pytest.mark.parametrize("text", AUTHORIZATION_SCHEMES)
    def test_non_bearer_authorization_values_are_detected_and_redacted(self, text):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "[REDACTED]" in redacted
        assert "ZmFrZS1wYXNz" not in redacted
        assert "FAKEtoken0123456789" not in redacted

    @pytest.mark.parametrize(
        "text",
        [
            "AKIAIOSFODNN7EXAMPLE",
            # Directly adjacent to a word character: still detected.
            "prefixAKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_aws_access_key_identifiers_are_detected_and_redacted(self, text):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted

    def test_jwt_like_strings_are_detected_and_redacted(self):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.FAKEsignature"
        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    def test_slack_like_tokens_are_detected_and_redacted(self):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        text = "xoxb-123456789012-abcdefghijklmnop"
        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "xoxb-123456789012-abcdefghijklmnop" not in redacted

    def test_url_basic_authentication_is_detected_and_redacted(self):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        text = "https://user:FAKEpass123@example.invalid/path"
        assert contains_credential(text) is True
        redacted = redact_secrets(text)
        assert "FAKEpass123" not in redacted
        # The user and the host stay readable.
        assert "user" in redacted
        assert "example.invalid" in redacted

    BENIGN = [
        "task-oriented approach",
        "the risk-oriented team",
        "sk-1",
        # Ordinary source code must survive untouched.
        'password = os.getenv("PW")',
        'password = getpass("x")',
        "secret = None",
        "token = 1234",
        "the secret sauce",
        "secret: 42",
        "import os\npassword = my_password\n",
        "https://example.invalid/path",
        "http://localhost:3000/@user",
        "git+ssh://git@github.com",
        'authorization = "none"',
    ]

    @pytest.mark.parametrize("text", BENIGN)
    def test_benign_text_and_ordinary_code_are_not_redacted(self, text):
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        assert contains_credential(text) is False
        assert redact_secrets(text) == text

    def test_the_benchmark_task_corpus_is_never_redacted(self):
        """The 20 benchmark task files must be unaffected by the detector."""
        from benchmarks.benchmark.provider import contains_credential, redact_secrets

        task_root = os.path.join(_ROOT, "benchmarks", "data", "tasks")
        task_ids = sorted(
            name for name in os.listdir(task_root) if name.startswith("HE-EDIT-")
        )
        assert len(task_ids) == 20

        scanned = 0
        for task_id in task_ids:
            task_dir = os.path.join(task_root, task_id)
            for name in sorted(os.listdir(task_dir)):
                path = os.path.join(task_dir, name)
                if not os.path.isfile(path):
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                scanned += 1
                assert contains_credential(text) is False, path
                assert redact_secrets(text) == text, path
        assert scanned >= 80

    def test_structured_metadata_is_scrubbed_for_the_new_shapes(self):
        from benchmarks.benchmark.artifacts import _scrub_mapping

        scrubbed = _scrub_mapping(
            {
                "note": "password=FAKEpass123456",
                "url": "https://user:FAKEpass123@example.invalid/path",
                "nested": {"aws": "AKIAIOSFODNN7EXAMPLE", "safe": "kept"},
            }
        )
        assert "FAKEpass123456" not in scrubbed["note"]
        assert "FAKEpass123" not in scrubbed["url"]
        assert "AKIAIOSFODNN7EXAMPLE" not in scrubbed["nested"]["aws"]
        assert scrubbed["nested"]["safe"] == "kept"

    def test_no_synthetic_secret_of_the_new_shapes_reaches_any_artefact(
        self, repository, tmp_path
    ):
        """End-to-end: a new-shape secret must not survive in any artefact."""
        secret = "AKIAIOSFODNN7EXAMPLE"
        leaky_candidate = (
            "def below_zero(operations):\n"
            "    # aws " + secret + "\n"
            "    return False\n"
        )
        leaky_response = (
            "Here is the repair, using key password=FAKEpass123456\n"
            "```python\n" + leaky_candidate + "```"
        )
        provider = ScriptedProvider(leaky_response)
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        blob = ""
        for root, _dirs, files in os.walk(tmp_path / "run"):
            for name in files:
                with open(os.path.join(root, name), "r", encoding="utf-8", errors="replace") as fh:
                    blob += fh.read()

        assert secret not in blob
        assert "FAKEpass123456" not in blob
        assert record.candidate_redacted is True
        # The record still identifies the bytes that were evaluated.
        assert record.candidate_sha256 != record.candidate_file_sha256


class TestArtifactRootMetadata:
    """The reported artefact root must be the directory that holds the files."""

    def test_a_fresh_output_directory_reports_the_plain_artifacts_root(
        self, repository, tmp_path
    ):
        provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        runner, summary = _run(repository, provider, tmp_path / "run")
        record = _records(runner.output_paths()["attempts_jsonl"])[0]

        assert summary["artifacts_dir"] == "artifacts"
        metadata = json.load(
            open(runner.output_paths()["run_metadata_json"], encoding="utf-8")
        )
        assert metadata["artifacts_dir"] == "artifacts"
        # The reported root really contains the attempt directory.
        attempt_dir = os.path.join(tmp_path / "run", summary["artifacts_dir"], record.attempt_id)
        assert os.path.isdir(attempt_dir)
        assert os.path.exists(os.path.join(attempt_dir, "candidate.py"))

    def test_a_reused_output_directory_reports_the_run_scoped_root(
        self, repository, tmp_path
    ):
        """Regression: the metadata used to point at the stale shared root."""
        first_provider = ScriptedProvider(f"```python\n{CORRECT_BELOW_ZERO}```")
        first, first_summary = _run(
            repository, first_provider, tmp_path / "run", allow_overwrite=True
        )
        first_record = _records(first.output_paths()["attempts_jsonl"])[0]

        second_provider = ScriptedProvider(f"```python\n{WRONG_BELOW_ZERO}```")
        second, second_summary = _run(
            repository, second_provider, tmp_path / "run", allow_overwrite=True
        )
        second_record = _records(second.output_paths()["attempts_jsonl"])[0]

        # The first run owns the plain root, the second run a run-scoped one.
        assert first_summary["artifacts_dir"] == "artifacts"
        assert second_summary["artifacts_dir"] == f"artifacts-{second.run_id}"
        assert second.run_id != first.run_id

        metadata = json.load(
            open(second.output_paths()["run_metadata_json"], encoding="utf-8")
        )
        assert metadata["artifacts_dir"] == second_summary["artifacts_dir"]

        # Both reported roots really contain their attempt directory.
        for summary, record in (
            (first_summary, first_record),
            (second_summary, second_record),
        ):
            attempt_dir = os.path.join(
                tmp_path / "run", summary["artifacts_dir"], record.attempt_id
            )
            assert os.path.isdir(attempt_dir)
            assert os.path.exists(os.path.join(attempt_dir, "attempt.json"))
            # The record's own path agrees with the reported root.
            assert record.artifacts_path.startswith(summary["artifacts_dir"] + "/")

        # The two runs really do live in different roots.
        assert first_record.artifacts_path != second_record.artifacts_path
        assert os.path.isdir(os.path.join(tmp_path / "run", "artifacts"))
        assert os.path.isdir(os.path.join(tmp_path / "run", f"artifacts-{second.run_id}"))


class TestStderrRedactionBeforeTruncation:

    def test_a_truncated_credential_cannot_survive(self, repository, tmp_path):
        """
        The stderr tail is truncated to 500 characters. If the truncation
        happened before redaction, a credential straddling the cut would
        leave an unmatchable fragment behind.
        """
        import re as _re

        from benchmarks.benchmark.pipeline import AttemptPipeline
        from benchmarks.benchmark.adapters import get_adapter

        # The secret is placed so that the 500-character window cuts through it.
        payload = "A" * 100 + FAKE_CREDENTIAL + "B" * 500
        candidate = (
            "def below_zero(operations):\n"
            "    import sys\n"
            "    sys.stderr.write('%s')\n" % payload +
            "    return False\n"
        )
        provider = ScriptedProvider(f"```python\n{candidate}```")
        pipeline = AttemptPipeline(
            provider=provider,
            adapter=get_adapter("full_file_regeneration"),
            mode="live",
            run_id="stderr-test",
        )
        outcome = pipeline.run(repository.get("HE-EDIT-006"), 1)
        tail = outcome.evaluation.get("stderr_tail") or ""

        assert len(tail) <= 500
        assert FAKE_CREDENTIAL not in tail
        assert not _re.search(r"(?i)sk-[A-Za-z0-9_-]{20,}", tail)
        # No fragment of the secret may survive either.
        assert "FAKE0123456789abcdef" not in tail

