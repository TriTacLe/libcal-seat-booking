from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture() -> "callable[[str], str]":
    def read(name: str) -> str:
        return (FIXTURES / name).read_text()

    return read
