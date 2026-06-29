#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.config import Settings, get_settings  # noqa: E402
from app.eval.question_bank import load_question_bank_cases  # noqa: E402
from app.eval.suite import (  # noqa: E402
    EVALUATION_PROFILE_SMOKE,
    EVALUATION_PROFILE_STRICT,
    EVALUATION_PROFILE_WORKFLOW,
    EXECUTION_MODE_DETERMINISTIC_MOCK,
    EXECUTION_MODE_LIVE_PROVIDER,
    run_question_bank_suite,
    select_stratified_cases,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the PLC eval question bank locally.')
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'data' / 'eval_runs' / 'latest')
    parser.add_argument('--database-url', default=None)
    parser.add_argument(
        '--mode',
        choices=[EXECUTION_MODE_DETERMINISTIC_MOCK, EXECUTION_MODE_LIVE_PROVIDER],
        default=EXECUTION_MODE_DETERMINISTIC_MOCK,
        help='deterministic_mock uses the scripted runner; live_provider uses the real Main Agent provider.',
    )
    parser.add_argument(
        '--profile',
        choices=[
            EVALUATION_PROFILE_STRICT,
            EVALUATION_PROFILE_SMOKE,
            EVALUATION_PROFILE_WORKFLOW,
        ],
        default=None,
        help=(
            'strict checks exact route sequence; smoke checks provider/runtime '
            'liveness; workflow separates connectivity, required sequence, and '
            'over-orchestration.'
        ),
    )
    parser.add_argument(
        '--case-id',
        action='append',
        default=None,
        help='Run only the specified case id. Can be repeated.',
    )
    parser.add_argument('--sample-size', type=int, default=None)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--mcp-mode', choices=['mock', 'real', 'hybrid', 'subagent'], default=None)
    parser.add_argument('--model', default=None)
    parser.add_argument('--max-turns', type=int, default=None)
    parser.add_argument(
        '--subagent-timeout-seconds',
        type=int,
        default=None,
        help=(
            'Override SUBAGENT_TIMEOUT_SECONDS for eval runs. Useful for full '
            'live subagent runs so one slow SSE call does not block the suite.'
        ),
    )
    parser.add_argument(
        '--stop-on-failure',
        action='store_true',
        help='Stop after the first failed case; useful for slow live-provider smoke runs.',
    )
    parser.add_argument(
        '--capture-provider-transcript',
        action='store_true',
        help=(
            'Opt in to saving raw provider request/response messages in case '
            'transcripts. Intended for live-provider debugging.'
        ),
    )
    parser.add_argument('--list-cases', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == EXECUTION_MODE_LIVE_PROVIDER:
        _load_env_file(args.env_file)
        os.environ.setdefault("MAIN_AGENT_HTTP_BACKEND", "auto")
        if args.capture_provider_transcript:
            os.environ["MAIN_AGENT_CAPTURE_PROVIDER_TRANSCRIPT"] = "true"
        get_settings.cache_clear()
    cases = load_question_bank_cases()
    if args.case_id:
        wanted_ids = set(args.case_id)
        cases = [case for case in cases if case.id in wanted_ids]
        missing_ids = wanted_ids - {case.id for case in cases}
        if missing_ids:
            print(
                "Unknown case id(s): " + ", ".join(sorted(missing_ids)),
                file=sys.stderr,
            )
            return 2
    if args.sample_size is not None:
        cases = select_stratified_cases(cases, sample_size=args.sample_size)
    if args.list_cases:
        for case in cases:
            print(f"{case.id}\t{case.expected_route}\t{case.topic_family}")
        return 0
    profile = args.profile or (
        EVALUATION_PROFILE_WORKFLOW
        if args.mode == EXECUTION_MODE_LIVE_PROVIDER
        else EVALUATION_PROFILE_STRICT
    )
    result = run_question_bank_suite(
        cases=cases,
        run_dir=args.run_dir,
        database_url=args.database_url,
        execution_mode=args.mode,
        settings=Settings(),
        evaluation_profile=profile,
        mcp_mode=args.mcp_mode,
        model=args.model,
        max_turns=args.max_turns,
        subagent_timeout_seconds=args.subagent_timeout_seconds,
        stop_on_failure=args.stop_on_failure,
    )
    print(result.summary)
    print(result.markdown_report_path)
    print(result.json_report_path)
    print(result.inspect_log_path)
    print(result.html_report_path)
    print(result.transcript_dir)
    return 0


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


if __name__ == '__main__':
    raise SystemExit(main())
