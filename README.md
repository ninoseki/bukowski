# bukowski

A `pyproject.toml` conversion tool for [Poetry](https://python-poetry.org/) to [uv](https://docs.astral.sh/uv/) migration.

## Installation

```bash
pip install bukowski
# or
uv pip install bukowski
```

## Usage

```bash
$ uv run bukowski --help

 Usage: bukowski [OPTIONS] [PATH]

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│   path      [PATH]  path to pyproject.toml [default: pyproject.toml]         │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --force-overwrite     -f        Whether to overwrite the existing            │
│                                 pyproject.toml file or not                   │
│ --install-completion            Install completion for the current shell.    │
│ --show-completion               Show completion for the current shell, to    │
│                                 copy it or customize the installation.       │
│ --help                          Show this message and exit.                  │
╰──────────────────────────────────────────────────────────────────────────────╯
```

For example, let's say you have the following `pyproject.toml`:

```toml
[tool.poetry]
name = "foo"
version = "0.1.0"
description = "bar"
authors = ["John Smith <johnsmith@example.org>"]
readme = "README.md"

[tool.poetry.dependencies]
python = "^3.10"
fastapi = { extras = ["all"], version = "^0.112.2" }
requests = "2.32.3"

[tool.poetry.group.dev.dependencies]
pytest = "^8.3.2"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

`bukowski /path/to/pyproject.toml` converts it (outputs it to stdout when `-f` options is not set, otherwise overwrites the path) as follows:

```toml
[project]
name = "foo"
version = "0.1.0"
description = "bar"
readme = "README.md"
requires-python = ">=3.10,<4.0"
license = ""
authors = [
    { name = "John Smith", email = "johnsmith@example.org" },
]
dependencies = [
    "fastapi[all]>=0.112.2,<0.113.0",
    "requests==2.32.3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.3.2,<9.0.0",
]
```

Then you can do `uv sync` with the new `pyproject.toml`.

## Known Issues

- `packages` (`tool.poetry.packages`) is not supported.
- `source` (`tool.poetry.source`) is supported, but the conversion may be lossy.

## Alternatives

- [PacificGilly/poetry_to_uv](https://github.com/PacificGilly/poetry_to_uv)
