import tempfile
from pathlib import Path

import pytest
import tomlkit
from poetry.core.poetry import Poetry

from bukowski.factory import Factory
from bukowski.main import poetry_to_uv


@pytest.fixture
def poetry():
    return Factory().create_poetry(Path("tests/fixtures/poetry/pyproject.toml"))


@pytest.fixture
def poetry_with_non_package_mode():
    data = tomlkit.loads(Path("tests/fixtures/poetry/pyproject.toml").read_text())
    data["tool"]["poetry"]["package-mode"] = False  # type: ignore

    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(tomlkit.dumps(data))
        f.close()

        yield Factory().create_poetry(Path(f.name))


def test_poetry_to_uv(poetry: Poetry):
    uv = poetry_to_uv(poetry)
    expected = tomlkit.loads(Path("tests/fixtures/uv/pyproject.toml").read_text())
    assert uv == expected


def test_poetry_to_uv_with_non_package_mode(poetry_with_non_package_mode: Poetry):
    uv = poetry_to_uv(poetry_with_non_package_mode)
    expected = tomlkit.loads(Path("tests/fixtures/uv/pyproject.toml").read_text())
    assert uv != expected
    assert "build-system" not in uv
