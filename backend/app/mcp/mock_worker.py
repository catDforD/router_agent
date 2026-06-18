"""Deterministic in-process mock PLC workers for local Router execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from app.core.time import utc_now
from app.models.router_schema import (
    ArtifactRef,
    ArtifactType,
    ArtifactVisibility,
    ClarificationQuestion,
    ClarificationRequest,
    Diagnostic,
    Failure,
    FailureReproduction,
    FailureStatus,
    FormalMetrics,
    NextRecommendedAction,
    RepairMetrics,
    Severity,
    TestMetrics,
    WorkerInput,
    WorkerMetrics,
    WorkerOutcome,
    WorkerOutcomeStatus,
    WorkerType,
)


DEFAULT_MOCK_SCENARIO = "dev_test_pass"
SCENARIO_DEV_TEST_PASS = "dev_test_pass"
SCENARIO_TEST_FAILED_THEN_REPAIR_PASS = "test_failed_then_repair_pass"
SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED = "test_failed_repair_exhausted"
SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS = "formal_failed_then_repair_pass"
SCENARIO_NEED_CLARIFICATION = "need_clarification"
SCENARIO_WORKER_TIMEOUT = "worker_timeout"


@dataclass(frozen=True)
class MockArtifactWriteIntent:
    """Artifact content and metadata that the adapter will persist."""

    artifact_type: ArtifactType | str
    version: int
    name: str
    content: Any
    summary: str
    visibility: ArtifactVisibility | str = ArtifactVisibility.USER
    metadata: dict[str, Any] | None = None
    parent_artifact_ids: tuple[str, ...] = ()
    mime_type: str | None = None


@dataclass(frozen=True)
class MockWorkerOutput:
    """Contract-focused mock output before persistence and final normalization."""

    outcome: WorkerOutcome
    summary: str
    artifact_writes: tuple[MockArtifactWriteIntent, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    failures: tuple[Failure, ...] = ()
    clarification_request: ClarificationRequest | None = None
    metrics: WorkerMetrics = field(default_factory=WorkerMetrics)
    next_recommended_action: NextRecommendedAction | str = NextRecommendedAction.NONE
    metadata: dict[str, Any] | None = None


class MockWorkerTimeout(Exception):
    """Raised by the deterministic timeout scenario."""


class MockWorkerSchemaInvalid(Exception):
    """Raised when a mock path intentionally simulates invalid worker output."""


class MockWorkerExecutionError(Exception):
    """Raised for deterministic mock execution failures."""


def run_mock_worker(
    worker_input: WorkerInput,
    *,
    scenario: str = DEFAULT_MOCK_SCENARIO,
) -> MockWorkerOutput:
    """Run one deterministic mock worker for a validated WorkerInput."""

    if scenario == SCENARIO_WORKER_TIMEOUT:
        raise MockWorkerTimeout("mock worker timed out")

    if scenario == SCENARIO_NEED_CLARIFICATION:
        return _need_clarification(worker_input, scenario=scenario)

    worker_type = _value(worker_input.worker_type)
    if worker_type == WorkerType.PLC_DEV.value:
        return _plc_dev(worker_input, scenario=scenario)
    if worker_type == WorkerType.PLC_TEST.value:
        return _plc_test(worker_input, scenario=scenario)
    if worker_type == WorkerType.PLC_FORMAL.value:
        return _plc_formal(worker_input, scenario=scenario)
    if worker_type == WorkerType.PLC_REPAIR.value:
        return _plc_repair(worker_input, scenario=scenario)

    raise MockWorkerExecutionError(f"unsupported worker_type: {worker_type}")


def _plc_dev(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    parent_ids = _input_artifact_ids(worker_input)
    language = worker_input.context.target_plc_language or "ST"
    platform = worker_input.context.target_platform or "Codesys"
    requirements = {
        "schema_version": "router.v1",
        "goal": worker_input.context.user_goal,
        "requirements": [
            {
                "id": "REQ-001",
                "text": "Start command shall run the motor when safe.",
            },
            {
                "id": "REQ-002",
                "text": "Stop, emergency stop, or fault shall force MotorRun false.",
            },
        ],
        "target_plc_language": language,
        "target_platform": platform,
        "mock_scenario": scenario,
    }
    code = (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartCmd : BOOL;\n"
        "    StopCmd : BOOL;\n"
        "    EmergencyStop : BOOL;\n"
        "    FaultActive : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "END_VAR\n\n"
        "IF StopCmd OR EmergencyStop OR FaultActive THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartCmd THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "END_FUNCTION_BLOCK\n"
    )
    io_contract = {
        "inputs": ["StartCmd", "StopCmd", "EmergencyStop", "FaultActive"],
        "outputs": ["MotorRun"],
        "entry_function_block": "FB_MotorControl",
    }

    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
            confidence=0.9,
            reason="Mock PLC development completed.",
        ),
        summary="Mock plc-dev produced requirements, PLC code, and I/O contract.",
        artifact_writes=(
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.REQUIREMENTS_IR,
                version=1,
                name="requirements_ir_v1.json",
                content=requirements,
                summary="Mock requirements IR for PLC development.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "target_plc_language": language,
                    "target_platform": platform,
                    "tags": ["mock", "requirements"],
                },
                mime_type="application/json",
            ),
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.PLC_CODE,
                version=1,
                name="plc_code_v1.st",
                content=code,
                summary="Mock Structured Text implementation.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "target_plc_language": language,
                    "target_platform": platform,
                    "code_metadata": {
                        "code_version": 1,
                        "is_current": True,
                        "compile_status": "unknown",
                        "entry_function_block": "FB_MotorControl",
                    },
                    "tags": ["mock", "plc"],
                },
                mime_type="text/plain",
            ),
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.IO_CONTRACT,
                version=1,
                name="io_contract_v1.json",
                content=io_contract,
                summary="Mock I/O contract for generated PLC code.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "target_plc_language": language,
                    "target_platform": platform,
                    "tags": ["mock", "io_contract"],
                },
                mime_type="application/json",
            ),
        ),
        diagnostics=(
            _diagnostic(
                severity=Severity.INFO,
                code="MOCK_DEV_COMPLETED",
                message="Mock PLC development completed successfully.",
            ),
        ),
        metrics=WorkerMetrics(duration_ms=120),
        next_recommended_action=NextRecommendedAction.TEST,
        metadata={"mock_scenario": scenario},
    )


def _plc_test(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    code_ref = _artifact_ref(worker_input, ArtifactType.PLC_CODE)
    should_fail = (
        scenario
        in {
            SCENARIO_TEST_FAILED_THEN_REPAIR_PASS,
            SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED,
        }
        and code_ref is not None
        and (
            scenario == SCENARIO_TEST_FAILED_REPAIR_EXHAUSTED
            or code_ref.version <= 1
        )
    )
    if should_fail:
        return _plc_test_failed(worker_input, scenario=scenario)
    return _plc_test_passed(worker_input, scenario=scenario)


def _plc_test_passed(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    parent_ids = _input_artifact_ids(worker_input)
    report = {
        "status": "passed",
        "total": 4,
        "passed": 4,
        "failed": 0,
        "cases": [
            "start_runs_motor",
            "stop_forces_motor_off",
            "emergency_stop_forces_motor_off",
            "fault_forces_motor_off",
        ],
        "mock_scenario": scenario,
    }
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
            confidence=0.96,
            reason="All mock PLC tests passed.",
        ),
        summary="Mock plc-test passed all generated checks.",
        artifact_writes=(
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.TEST_REPORT,
                version=_report_version(worker_input, ArtifactType.TEST_REPORT),
                name="test_report.json",
                content=report,
                summary="Mock passing PLC test report.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "test_metadata": {
                        "total": 4,
                        "passed": 4,
                        "failed": 0,
                        "coverage_score": 0.85,
                        "status": "passed",
                    },
                    "tags": ["mock", "test", "passed"],
                },
                mime_type="application/json",
            ),
        ),
        diagnostics=(
            _diagnostic(
                severity=Severity.INFO,
                code="MOCK_TEST_PASSED",
                message="Mock PLC tests passed.",
            ),
        ),
        metrics=WorkerMetrics(
            duration_ms=90,
            test_metrics=TestMetrics(
                total=4,
                passed=4,
                failed=0,
                skipped=0,
                coverage_score=0.85,
            ),
        ),
        next_recommended_action=NextRecommendedAction.RUN_QUALITY_GATE,
        metadata={"mock_scenario": scenario},
    )


def _plc_test_failed(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    now = utc_now()
    parent_ids = _input_artifact_ids(worker_input)
    report_version = _report_version(worker_input, ArtifactType.TEST_REPORT)
    report = {
        "status": "failed",
        "total": 4,
        "passed": 3,
        "failed": 1,
        "failed_case": "emergency_stop_forces_motor_off",
        "mock_scenario": scenario,
    }
    trace = {
        "case": "emergency_stop_forces_motor_off",
        "steps": [
            {"StartCmd": True, "EmergencyStop": False, "MotorRun": True},
            {"StartCmd": True, "EmergencyStop": True, "MotorRun": True},
        ],
        "expected": "MotorRun false when EmergencyStop is true.",
        "actual": "MotorRun remained true.",
    }
    report_intent = MockArtifactWriteIntent(
        artifact_type=ArtifactType.TEST_REPORT,
        version=report_version,
        name="test_report_failed.json",
        content=report,
        summary="Mock failing PLC test report.",
        parent_artifact_ids=parent_ids,
        metadata={
            "test_metadata": {
                "total": 4,
                "passed": 3,
                "failed": 1,
                "coverage_score": 0.85,
                "status": "failed",
            },
            "tags": ["mock", "test", "failed"],
        },
        mime_type="application/json",
    )
    trace_intent = MockArtifactWriteIntent(
        artifact_type=ArtifactType.FAILING_TRACE,
        version=report_version,
        name="failing_trace.json",
        content=trace,
        summary="Mock failing trace for emergency stop behavior.",
        parent_artifact_ids=parent_ids,
        metadata={"tags": ["mock", "test", "failing_trace"]},
        mime_type="application/json",
    )
    failure = Failure(
        failure_id=_mock_id("failure"),
        source="test",
        severity=Severity.BLOCKING,
        title="Emergency stop test failed",
        description="The generated code did not force MotorRun false under emergency stop.",
        expected="MotorRun is false when EmergencyStop is true.",
        actual="MotorRun remained true.",
        reproduction=FailureReproduction(steps=["Run emergency_stop_forces_motor_off."]),
        evidence_artifact_ids=[],
        status=FailureStatus.OPEN,
        created_by_worker_job_id=worker_input.worker_job_id,
        created_at=now,
    )
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.FAILED,
            blocking=True,
            confidence=0.95,
            reason="One mock PLC test failed.",
        ),
        summary="Mock plc-test found one blocking failure.",
        artifact_writes=(report_intent, trace_intent),
        diagnostics=(
            _diagnostic(
                severity=Severity.ERROR,
                code="MOCK_TEST_FAILED",
                message="Emergency stop did not force MotorRun false.",
            ),
        ),
        failures=(failure,),
        metrics=WorkerMetrics(
            duration_ms=95,
            test_metrics=TestMetrics(
                total=4,
                passed=3,
                failed=1,
                skipped=0,
                coverage_score=0.85,
            ),
        ),
        next_recommended_action=NextRecommendedAction.REPAIR,
        metadata={"mock_scenario": scenario},
    )


def _plc_formal(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    code_ref = _artifact_ref(worker_input, ArtifactType.PLC_CODE)
    should_fail = (
        scenario == SCENARIO_FORMAL_FAILED_THEN_REPAIR_PASS
        and code_ref is not None
        and code_ref.version <= 1
    )
    if should_fail:
        return _plc_formal_failed(worker_input, scenario=scenario)
    return _plc_formal_passed(worker_input, scenario=scenario)


def _plc_formal_passed(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    parent_ids = _input_artifact_ids(worker_input)
    report = {
        "status": "passed",
        "properties": [
            "EmergencyStop -> NOT MotorRun",
            "FaultActive -> NOT MotorRun",
            "StopCmd -> NOT MotorRun",
        ],
        "total_properties": 3,
        "passed_properties": 3,
        "failed_properties": 0,
        "mock_scenario": scenario,
    }
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
            confidence=0.93,
            reason="All mock formal properties passed.",
        ),
        summary="Mock plc-formal verified all safety properties.",
        artifact_writes=(
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.FORMAL_REPORT,
                version=_report_version(worker_input, ArtifactType.FORMAL_REPORT),
                name="formal_report.json",
                content=report,
                summary="Mock passing formal verification report.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "formal_metadata": {
                        "total_properties": 3,
                        "passed_properties": 3,
                        "failed_properties": 0,
                        "status": "passed",
                    },
                    "tags": ["mock", "formal", "passed"],
                },
                mime_type="application/json",
            ),
        ),
        diagnostics=(
            _diagnostic(
                severity=Severity.INFO,
                code="MOCK_FORMAL_PASSED",
                message="Mock formal verification passed.",
            ),
        ),
        metrics=WorkerMetrics(
            duration_ms=110,
            formal_metrics=FormalMetrics(
                total_properties=3,
                passed_properties=3,
                failed_properties=0,
                unknown_properties=0,
            ),
        ),
        next_recommended_action=NextRecommendedAction.RUN_QUALITY_GATE,
        metadata={"mock_scenario": scenario},
    )


def _plc_formal_failed(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    now = utc_now()
    parent_ids = _input_artifact_ids(worker_input)
    report_version = _report_version(worker_input, ArtifactType.FORMAL_REPORT)
    report = {
        "status": "failed",
        "failed_property": "EmergencyStop -> NOT MotorRun",
        "total_properties": 3,
        "passed_properties": 2,
        "failed_properties": 1,
        "mock_scenario": scenario,
    }
    counterexample = {
        "property": "EmergencyStop -> NOT MotorRun",
        "trace": [
            {"t": 0, "StartCmd": True, "EmergencyStop": False, "MotorRun": True},
            {"t": 1, "StartCmd": True, "EmergencyStop": True, "MotorRun": True},
        ],
    }
    failure = Failure(
        failure_id=_mock_id("failure"),
        source="formal",
        severity=Severity.BLOCKING,
        title="Formal emergency stop property failed",
        description="A counterexample keeps MotorRun true after EmergencyStop becomes true.",
        expected="EmergencyStop implies MotorRun is false.",
        actual="Counterexample shows MotorRun true.",
        reproduction=FailureReproduction(
            steps=["Replay the mock counterexample trace."]
        ),
        evidence_artifact_ids=[],
        status=FailureStatus.OPEN,
        created_by_worker_job_id=worker_input.worker_job_id,
        created_at=now,
    )
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.FAILED,
            blocking=True,
            confidence=0.94,
            reason="One mock formal property failed.",
        ),
        summary="Mock plc-formal produced one blocking counterexample.",
        artifact_writes=(
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.FORMAL_REPORT,
                version=report_version,
                name="formal_report_failed.json",
                content=report,
                summary="Mock failing formal verification report.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "formal_metadata": {
                        "total_properties": 3,
                        "passed_properties": 2,
                        "failed_properties": 1,
                        "status": "failed",
                    },
                    "tags": ["mock", "formal", "failed"],
                },
                mime_type="application/json",
            ),
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.COUNTEREXAMPLE,
                version=report_version,
                name="counterexample.json",
                content=counterexample,
                summary="Mock counterexample for emergency stop property.",
                parent_artifact_ids=parent_ids,
                metadata={"tags": ["mock", "formal", "counterexample"]},
                mime_type="application/json",
            ),
        ),
        diagnostics=(
            _diagnostic(
                severity=Severity.ERROR,
                code="MOCK_FORMAL_FAILED",
                message="Emergency stop safety property has a counterexample.",
            ),
        ),
        failures=(failure,),
        metrics=WorkerMetrics(
            duration_ms=115,
            formal_metrics=FormalMetrics(
                total_properties=3,
                passed_properties=2,
                failed_properties=1,
                unknown_properties=0,
            ),
        ),
        next_recommended_action=NextRecommendedAction.REPAIR,
        metadata={"mock_scenario": scenario},
    )


def _plc_repair(worker_input: WorkerInput, *, scenario: str) -> MockWorkerOutput:
    code_ref = _artifact_ref(worker_input, ArtifactType.PLC_CODE)
    current_version = code_ref.version if code_ref is not None else 1
    next_version = current_version + 1
    repair_round = worker_input.context.repair_round or max(1, next_version - 1)
    parent_ids = _input_artifact_ids(worker_input)
    patch = (
        "--- plc_code_v1.st\n"
        "+++ plc_code_v2.st\n"
        "@@\n"
        "-ELSIF StartCmd THEN\n"
        "+ELSIF StartCmd AND NOT EmergencyStop AND NOT FaultActive THEN\n"
        "     MotorRun := TRUE;\n"
    )
    patched_code = (
        "FUNCTION_BLOCK FB_MotorControl\n"
        "VAR_INPUT\n"
        "    StartCmd : BOOL;\n"
        "    StopCmd : BOOL;\n"
        "    EmergencyStop : BOOL;\n"
        "    FaultActive : BOOL;\n"
        "END_VAR\n"
        "VAR_OUTPUT\n"
        "    MotorRun : BOOL;\n"
        "END_VAR\n\n"
        "IF StopCmd OR EmergencyStop OR FaultActive THEN\n"
        "    MotorRun := FALSE;\n"
        "ELSIF StartCmd AND NOT EmergencyStop AND NOT FaultActive THEN\n"
        "    MotorRun := TRUE;\n"
        "END_IF;\n"
        "END_FUNCTION_BLOCK\n"
    )
    summary = {
        "repair_round": repair_round,
        "from_code_artifact_id": code_ref.artifact_id if code_ref else None,
        "to_code_version": next_version,
        "changes": [
            "Guarded the start branch with emergency stop and fault conditions.",
        ],
        "mock_scenario": scenario,
    }
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.PASSED,
            blocking=False,
            confidence=0.9,
            reason="Mock repair produced a patched code version.",
        ),
        summary="Mock plc-repair produced a patch and patched PLC code.",
        artifact_writes=(
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.PATCH,
                version=repair_round,
                name=f"patch_v{repair_round}.diff",
                content=patch,
                summary="Mock patch for PLC safety failure.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "patch_metadata": {
                        "from_code_artifact_id": code_ref.artifact_id if code_ref else None,
                        "changed_files": 1,
                        "changed_lines": 2,
                        "repair_round": repair_round,
                    },
                    "tags": ["mock", "repair", "patch"],
                },
                mime_type="text/x-diff",
            ),
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.PLC_CODE,
                version=next_version,
                name=f"plc_code_v{next_version}.st",
                content=patched_code,
                summary=f"Mock patched PLC code v{next_version}.",
                parent_artifact_ids=parent_ids,
                metadata={
                    "target_plc_language": worker_input.context.target_plc_language,
                    "target_platform": worker_input.context.target_platform,
                    "code_metadata": {
                        "code_version": next_version,
                        "is_current": True,
                        "compile_status": "unknown",
                        "entry_function_block": "FB_MotorControl",
                    },
                    "tags": ["mock", "repair", "plc"],
                },
                mime_type="text/plain",
            ),
            MockArtifactWriteIntent(
                artifact_type=ArtifactType.REPAIR_SUMMARY,
                version=repair_round,
                name=f"repair_summary_v{repair_round}.json",
                content=summary,
                summary="Mock repair summary.",
                parent_artifact_ids=parent_ids,
                metadata={"tags": ["mock", "repair", "summary"]},
                mime_type="application/json",
            ),
        ),
        diagnostics=(
            _diagnostic(
                severity=Severity.INFO,
                code="MOCK_REPAIR_COMPLETED",
                message="Mock repair produced a patched code version.",
            ),
        ),
        metrics=WorkerMetrics(
            duration_ms=130,
            repair_metrics=RepairMetrics(
                changed_files=1,
                changed_lines=2,
                patch_size_bytes=len(patch.encode("utf-8")),
            ),
        ),
        next_recommended_action=NextRecommendedAction.TEST,
        metadata={"mock_scenario": scenario},
    )


def _need_clarification(
    worker_input: WorkerInput,
    *,
    scenario: str,
) -> MockWorkerOutput:
    now = utc_now()
    question = ClarificationQuestion(
        question_id=_mock_id("question"),
        question="Which PLC platform and I/O signal names should be targeted?",
        reason="The mock worker requires platform and I/O details before producing code.",
        required=True,
        status="open",
        asked_at=now,
    )
    return MockWorkerOutput(
        outcome=WorkerOutcome(
            status=WorkerOutcomeStatus.NEED_CLARIFICATION,
            blocking=True,
            confidence=0.9,
            reason="The request is missing required PLC implementation details.",
        ),
        summary="Mock worker needs clarification before producing artifacts.",
        clarification_request=ClarificationRequest(
            blocking=True,
            questions=[question],
        ),
        metrics=WorkerMetrics(duration_ms=25),
        next_recommended_action=NextRecommendedAction.ASK_USER,
        metadata={"mock_scenario": scenario},
    )


def _diagnostic(*, severity: Severity, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        diagnostic_id=_mock_id("diagnostic"),
        severity=severity,
        code=code,
        message=message,
    )


def _report_version(worker_input: WorkerInput, artifact_type: ArtifactType) -> int:
    if artifact_type in {
        ArtifactType.TEST_REPORT,
        ArtifactType.FAILING_TRACE,
        ArtifactType.FORMAL_REPORT,
        ArtifactType.COUNTEREXAMPLE,
    }:
        code_ref = _artifact_ref(worker_input, ArtifactType.PLC_CODE)
        if code_ref is not None:
            return max(1, code_ref.version)

    same_type_versions = [
        artifact.version
        for artifact in worker_input.input_artifacts
        if _value(artifact.type) == artifact_type.value
    ]
    return max(same_type_versions, default=0) + 1


def _artifact_ref(
    worker_input: WorkerInput,
    artifact_type: ArtifactType,
) -> ArtifactRef | None:
    for artifact in worker_input.input_artifacts:
        if _value(artifact.type) == artifact_type.value:
            return artifact
    return None


def _input_artifact_ids(worker_input: WorkerInput) -> tuple[str, ...]:
    return tuple(artifact.artifact_id for artifact in worker_input.input_artifacts)


def _mock_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
