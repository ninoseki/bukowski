from pathlib import Path

from poetry.core.factory import Factory as _Factory
from poetry.core.poetry import Poetry
from poetry.core.pyproject.toml import PyProjectTOML


class Factory(_Factory):
    def create_poetry(self, poetry_file: Path, with_groups: bool = True) -> Poetry:
        local_config = PyProjectTOML(path=poetry_file).poetry_config

        check_result = self.validate(local_config)
        if check_result["errors"]:
            message = ""
            for error in check_result["errors"]:
                message += f"  - {error}\n"

            raise RuntimeError("The Poetry configuration is invalid:\n" + message)

        name = local_config.get("name", "non-package-mode")
        assert isinstance(name, str)

        version = local_config.get("version", "0")
        assert isinstance(version, str)

        package = self.get_package(name, version)
        package = self.configure_package(
            package, local_config, poetry_file.parent, with_groups=with_groups
        )

        return Poetry(poetry_file, local_config, package)
