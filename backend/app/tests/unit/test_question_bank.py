from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import get_engine_for_url, get_session_factory_for_url
from app.eval.question_bank import (
    EXECUTION_ROUTES,
    EXPECTED_ROUTE_COUNTS,
    REPO_ROOT,
    group_question_bank_cases,
    load_question_bank_cases,
)
from app.eval.suite import (
    _seed_code_content,
    _seed_context_files_for_case,
)
from app.models.db_models import Base
from app.repositories.task_repo import TaskRepository
from app.services.task_service import TaskService


@pytest.fixture()
def eval_seed_context(tmp_path: Path) -> Iterator[tuple[Settings, sessionmaker[Session]]]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'router-question-bank.db'}"
    engine = get_engine_for_url(database_url)
    Base.metadata.create_all(engine)
    factory = get_session_factory_for_url(database_url)
    settings = Settings(
        app_env="test",
        database_url=database_url,
        artifact_root=tmp_path / "artifacts",
    )
    try:
        yield settings, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_engine_for_url.cache_clear()
        get_session_factory_for_url.cache_clear()


def test_question_bank_loads_expected_80_case_distribution() -> None:
    cases = load_question_bank_cases()

    assert len(cases) == 80
    assert Counter(case.expected_route for case in cases) == EXPECTED_ROUTE_COUNTS
    assert "repair_after_formal_then_test_then_formal" not in {
        case.expected_route for case in cases
    }
    assert "repair_after_test_then_test" not in {
        case.expected_route for case in cases
    }


def test_execution_cases_have_valid_benchmark_metadata() -> None:
    cases = load_question_bank_cases()
    execution_cases = [
        case for case in cases if case.expected_route in EXECUTION_ROUTES
    ]

    assert len(execution_cases) == 40
    for case in execution_cases:
        assert case.benchmark_id
        assert case.benchmark_st_path
        assert case.validation_focus
        path = REPO_ROOT / case.benchmark_st_path
        assert path.is_file()
        assert (
            case.benchmark_id in case.benchmark_st_path
            or case.benchmark_st_path.startswith(
                "backend/app/tests/eval/formal_st_files/"
            )
        )


def test_formal_only_existing_code_uses_small_assertion_fixtures() -> None:
    cases = group_question_bank_cases(load_question_bank_cases())[
        "formal_only_existing_code"
    ]

    assert len(cases) == 10
    for case in cases:
        assert case.benchmark_st_path is not None
        assert case.benchmark_st_path.startswith(
            "backend/app/tests/eval/formal_st_files/"
        )
        assert case.formal_properties
        content = (REPO_ROOT / case.benchmark_st_path).read_text(encoding="utf-8")
        assert "//#ASSERT" in content


def test_existing_code_routes_seed_raw_benchmark_st_content() -> None:
    cases = load_question_bank_cases()
    seeded_routes = {
        "test_only_existing_code",
        "formal_only_existing_code",
    }

    for case in cases:
        if case.expected_route not in seeded_routes:
            continue
        assert case.benchmark_st_path is not None
        raw_content = (REPO_ROOT / case.benchmark_st_path).read_text(encoding="utf-8")
        assert _seed_code_content(case) == raw_content


def test_seed_context_writes_existing_code_as_current_code(
    eval_seed_context: tuple[Settings, sessionmaker[Session]],
) -> None:
    settings, session_factory = eval_seed_context
    case = group_question_bank_cases(load_question_bank_cases())[
        "test_only_existing_code"
    ][0]

    with session_factory() as session:
        created = TaskService(
            session=session,
            artifact_root=settings.artifact_root,
        ).create_task(message=case.message)
        session.commit()
        task_id = created.task.task_id

    _seed_context_files_for_case(settings, session_factory, task_id, case)

    with session_factory() as session:
        task = TaskRepository(session).get_task(task_id)
        assert task.workspace is not None
        assert task.current_files.current_code is not None
        seeded_code = (
            Path(task.workspace.root) / task.current_files.current_code
        ).read_text(encoding="utf-8")
        raw_code = (REPO_ROOT / case.benchmark_st_path).read_text(encoding="utf-8")
        assert seeded_code == raw_code
