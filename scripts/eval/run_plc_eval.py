#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.eval.question_bank import load_question_bank_cases  # noqa: E402
from app.eval.suite import run_question_bank_suite  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the PLC eval question bank locally.')
    parser.add_argument('--run-dir', type=Path, default=ROOT / 'data' / 'eval_runs' / 'latest')
    parser.add_argument('--database-url', default=None)
    parser.add_argument('--list-cases', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_question_bank_cases()
    if args.list_cases:
        for case in cases:
            print(f"{case.id}\t{case.expected_route}\t{case.topic_family}")
        return 0
    result = run_question_bank_suite(cases=cases, run_dir=args.run_dir, database_url=args.database_url)
    print(result.summary)
    print(result.markdown_report_path)
    print(result.json_report_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
