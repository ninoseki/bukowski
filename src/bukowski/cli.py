from pathlib import Path
from typing import Annotated

import tomlkit
import typer

from .factory import Factory
from .main import poetry_to_uv

app = typer.Typer()


def version_callback(value: bool):
    if value:
        from . import __version__

        print(__version__)  # noqa: T201

        raise typer.Exit()


@app.command()
def convert(
    path: Annotated[Path, typer.Argument(help="path to pyproject.toml")] = Path(
        "pyproject.toml"
    ),
    force_overwrite: Annotated[
        bool,
        typer.Option(
            "--force-overwrite",
            "-f",
            help="Whether to overwrite the existing pyproject.toml file or not",
        ),
    ] = False,
    _: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, help="Show version"),
    ] = None,
):
    poetry = Factory().create_poetry(path)
    pyproject = poetry_to_uv(poetry)

    converted = tomlkit.dumps(pyproject)
    if force_overwrite:
        path.write_text(converted)
    else:
        print(converted)  # noqa: T201


if __name__ == "__main__":
    typer.run(app())
