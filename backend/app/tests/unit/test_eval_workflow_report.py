from __future__ import annotations

from app.eval.report import EvalCaseResult, build_eval_report_payload, render_eval_html_report
from app.eval.suite import EvalTaskAudit, _extract_token_usage


def test_workflow_contract_metrics_are_reported() -> None:
    results = [
        EvalCaseResult(
            case_id="dev_then_test_converged",
            passed=True,
            task_id="task-ok",
            expected_statuses=["succeeded"],
            actual_status="succeeded",
            expected_route="dev_then_test",
            expected_worker_sequence=["plc-dev", "plc-test"],
            worker_sequence=["plc-dev", "plc-test"],
            worker_sequence_match=True,
            connectivity_pass=True,
            first_tool_pass=True,
            required_sequence_pass=True,
            over_orchestration=False,
            final_status_match=True,
            final_report_present=True,
            token_usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        ),
        EvalCaseResult(
            case_id="dev_then_test_over_orchestrated",
            passed=False,
            task_id="task-over",
            expected_statuses=["succeeded"],
            actual_status="failed",
            expected_route="dev_then_test",
            expected_worker_sequence=["plc-dev", "plc-test"],
            worker_sequence=["plc-dev", "plc-test", "plc-formal"],
            worker_sequence_match=False,
            connectivity_pass=True,
            first_tool_pass=True,
            required_sequence_pass=True,
            over_orchestration=True,
            final_status_match=False,
            failure_reason="main agent continued into extra PLC worker stages",
            token_usage={
                "input_tokens": 200,
                "output_tokens": 40,
                "total_tokens": 240,
            },
        ),
    ]

    payload = build_eval_report_payload(results)
    html = render_eval_html_report(results, evaluation_profile="workflow")

    assert payload["metrics"]["connectivity_pass_rate"] == 1.0
    assert payload["metrics"]["required_sequence_pass_rate"] == 1.0
    assert payload["metrics"]["over_orchestration_rate"] == 0.5
    assert payload["token_usage"]["scope"] == "main_agent_provider"
    assert payload["token_usage"]["cases_with_usage"] == 2
    assert payload["token_usage"]["totals"] == {
        "input_tokens": 300,
        "output_tokens": 60,
        "total_tokens": 360,
    }
    assert payload["token_usage"]["averages"]["total_tokens_per_case"] == 180
    assert payload["samples"][0]["token_usage"]["total_tokens"] == 120
    assert payload["samples"][1]["workflow_contract"] == {
        "connectivity_pass": True,
        "first_tool_pass": True,
        "required_sequence_pass": True,
        "over_orchestration": True,
        "final_status_match": False,
    }
    assert "Workflow Contract" in html
    assert "Token Usage" in html
    assert "Total Tokens" in html
    assert "Over-Orch" in html
    assert "over yes" in html


def test_token_usage_is_extracted_from_replay_log_totals() -> None:
    audit = EvalTaskAudit(
        task=None,  # type: ignore[arg-type]
        worker_jobs=[],
        artifacts=[],
        events=[],
        gate_results=[],
        final_report=None,
        replay_log={
            "entries": [
                {
                    "type": "token_usage",
                    "payload": {
                        "token_usage_delta": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                        "token_usage_total": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                },
                {
                    "type": "token_usage",
                    "payload": {
                        "token_usage_delta": {
                            "input_tokens": 7,
                            "output_tokens": 3,
                            "total_tokens": 10,
                        },
                        "token_usage_total": {
                            "input_tokens": 17,
                            "output_tokens": 8,
                            "total_tokens": 25,
                        },
                    },
                },
            ]
        },
        workspace_files=[],
    )

    assert _extract_token_usage(audit) == {
        "input_tokens": 17,
        "output_tokens": 8,
        "total_tokens": 25,
    }


def test_token_usage_extraction_falls_back_to_completed_event() -> None:
    audit = EvalTaskAudit(
        task=None,  # type: ignore[arg-type]
        worker_jobs=[],
        artifacts=[],
        events=[
            {
                "payload": {
                    "token_usage": {
                        "input_tokens": "14",
                        "output_tokens": "6",
                    }
                }
            }
        ],
        gate_results=[],
        final_report=None,
        replay_log=None,
        workspace_files=[],
    )

    assert _extract_token_usage(audit) == {
        "input_tokens": 14,
        "output_tokens": 6,
        "total_tokens": 20,
    }
