from pathlib import Path

import pytest

from egb.registry import Registry, load_registry
from egb.settings import BUILT_IN_ENTRIES


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--data-dir",
        type=Path,
        default=None,
        help="Data directory with a built bench-env, for the end-to-end test",
    )
    parser.addoption(
        "--gaib-root",
        type=Path,
        default=Path("../vendor/HARD-graph-benchmark"),
        help="HARD-graph-benchmark checkout",
    )


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry([BUILT_IN_ENTRIES])
