from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import tomlkit
from poetry.core.constraints.version import (
    Version,
    VersionConstraint,
    VersionUnion,
    parse_constraint,
)
from poetry.core.packages.dependency import Dependency
from poetry.core.poetry import Poetry
from returns.functions import raise_exception
from returns.pipeline import flow
from returns.pointfree import bind
from returns.result import ResultE, safe
from tomlkit.items import Array
from tomlkit.toml_document import TOMLDocument


@dataclass
class Ownership:
    name: str
    email: str


def serialize_ownership(ownership: Ownership) -> str:
    return "".join(
        [
            "{",
            " ",
            f'name = "{ownership.name}",',
            " ",
            f'email = "{ownership.email}"',
            " ",
            "},",
        ]
    )


def serialize_ownerships(ownerships: list[Ownership]) -> str:
    return "".join(
        [
            "[\n",
            "\n".join([serialize_ownership(ownership) for ownership in ownerships]),
            "\n",
            "]",
        ]
    )


def ownerships_to_array(
    ownerships: list[Ownership], *, multiline: bool = True
) -> Array:
    return tomlkit.array(serialize_ownerships(ownerships)).multiline(multiline)


def name_email_to_ownership(v: str) -> Ownership:
    name, email = v.split(" <")
    email = email[:-1]
    name = name.strip()
    return Ownership(name, email)


def strings_to_array(strings: Iterable[str], *, multiline: bool = True) -> Array:
    array = tomlkit.array()

    for string in strings:
        array.append(tomlkit.string(string))

    return array.multiline(multiline)


def normalize_constraint(
    constraint: VersionConstraint, pretty_constraint: str
) -> str | None:
    if isinstance(constraint, VersionUnion):
        if (
            constraint.excludes_single_version
            or constraint.excludes_single_wildcard_range
        ):
            return str(constraint)

        return ",".join(str(parse_constraint(c)) for c in pretty_constraint.split(","))

    if isinstance(constraint, Version):
        return f"=={constraint.text}"

    if not constraint.is_any():
        return str(constraint).replace(" ", "")
    return None


def dependency_to_pep508(dependency: Dependency) -> str:
    return "".join(
        [
            dependency.complete_pretty_name,
            normalize_constraint(dependency.constraint, dependency._pretty_constraint)
            or "",
        ]
    )


def get_tool_uv(pyproject: TOMLDocument):
    tool: dict[str, Any] | None = pyproject.get("tool")
    if tool is None:
        tool = tomlkit.table()
        pyproject["tool"] = tool

    uv: dict[str, Any] | None = tool.get("uv")
    if uv is None:
        uv = tomlkit.table()
        tool["uv"] = uv

    return uv


@safe
def init() -> TOMLDocument:
    pyproject: dict[str, Any] = tomlkit.document()
    pyproject["project"] = tomlkit.table()
    return pyproject


@safe
def set_project(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    package = poetry.package

    content = cast(dict[str, Any], pyproject["project"])

    content["name"] = package.name
    content["version"] = package.version.text
    content["description"] = package.description

    # NOTE: a dirty hack to set readme
    #       (package.readme is a Path object and it's not suitable for serialization)
    readme = poetry.pyproject.data.get("tool", {}).get("poetry", {}).get("readme")
    if readme:
        content["readme"] = readme

    constraint = parse_constraint(poetry.package.python_versions)
    content["requires-python"] = str(constraint)

    content["license"] = package.license.id if package.license else ""
    content["authors"] = ownerships_to_array(
        [name_email_to_ownership(author) for author in package.authors]
    )

    if package.maintainers:
        content["maintainers"] = ownerships_to_array(
            [name_email_to_ownership(maintainer) for maintainer in package.maintainers]
        )

    if package.keywords:
        content["keywords"] = strings_to_array(package.keywords)

    if package.classifiers:
        content["classifiers"] = strings_to_array(package.classifiers)

    if package.urls:
        content["urls"] = package.urls

    return pyproject


@safe
def set_scripts(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    scripts = poetry.pyproject.data.get("tool", {}).get("poetry", {}).get("scripts")

    if not scripts:
        return pyproject

    content = cast(dict[str, Any], pyproject["project"])
    content["scripts"] = scripts

    return pyproject


@safe
def set_main_dependencies(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    group = poetry.package._dependency_groups.get("main")
    if not group:
        return pyproject

    content = cast(dict[str, Any], pyproject["project"])

    pep508_dependencies = [
        dependency_to_pep508(dependency) for dependency in group.dependencies
    ]
    content["dependencies"] = strings_to_array(pep508_dependencies)

    return pyproject


@safe
def set_dev_dependencies(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    group = poetry.package._dependency_groups.get("dev")
    if not group:
        return pyproject

    pep508_dependencies = [
        dependency_to_pep508(dependency) for dependency in group.dependencies
    ]
    if not pep508_dependencies:
        return pyproject

    uv = get_tool_uv(pyproject)
    uv["dev-dependencies"] = strings_to_array(pep508_dependencies)

    return pyproject


@safe
def set_optional_dependencies(
    pyproject: TOMLDocument, *, poetry: Poetry
) -> TOMLDocument:
    filtered = [
        (key, group)
        for key, group in poetry.package._dependency_groups.items()
        if key not in ["main", "dev"]
    ]
    if not filtered:
        return pyproject

    content = cast(dict[str, Any], pyproject["project"])
    optional_dependencies = content.get("optional-dependencies")
    if not optional_dependencies:
        optional_dependencies = tomlkit.table()
        content["optional-dependencies"] = optional_dependencies

    for key, group in filtered:
        pep508_dependencies = [
            dependency_to_pep508(dependency) for dependency in group.dependencies
        ]
        optional_dependencies[key] = strings_to_array(pep508_dependencies)

    return pyproject


@safe
def set_extra_sections(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    # NOTE: use tomlkit to preserve comments and formatting
    with open(poetry.pyproject.path) as f:
        data = tomlkit.loads(f.read())

    if "build-system" in data:
        data.pop("build-system")

    poetry_tool: dict[str, Any] = data.pop("tool", {})
    if "poetry" in poetry_tool:
        poetry_tool.pop("poetry")

    for key, value in data.items():
        pyproject[key] = value

    if not poetry_tool:
        return pyproject

    tool = pyproject.get("tool")
    if not tool:
        tool = tomlkit.table()
        pyproject["tool"] = tool

    for key, value in poetry_tool.items():
        tool[key] = value

    return pyproject


@safe
def set_build_system(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    if not poetry.is_package_mode:
        return pyproject

    pyproject["build-system"] = {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }
    return pyproject


@safe
def set_index_urls(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    if not poetry.is_package_mode:
        return pyproject

    sources: list[dict] = (
        poetry.pyproject.data.get("tool", {}).get("poetry", {}).get("source", [])
    )
    if not sources:
        return pyproject

    index_url: str | None = None
    extra_index_url: set[str] = set()

    for source in sources:
        url: str | None = source.get("url")
        if url is None:
            continue

        priority: str | None = source.get("priority")
        default: bool | None = source.get("default")
        if priority in ["default", "primary"] or default:
            index_url = url
        else:
            extra_index_url.add(url)

    uv = get_tool_uv(pyproject)

    if index_url:
        uv["index-url"] = index_url

    if extra_index_url:
        uv["extra-index-url"] = strings_to_array(extra_index_url)

    return pyproject


def poetry_to_uv(poetry: Poetry) -> TOMLDocument:
    result: ResultE[TOMLDocument] = flow(
        init(),
        bind(
            partial(set_project, poetry=poetry),
        ),
        bind(partial(set_main_dependencies, poetry=poetry)),
        bind(partial(set_build_system, poetry=poetry)),
        bind(partial(set_dev_dependencies, poetry=poetry)),
        bind(partial(set_optional_dependencies, poetry=poetry)),
        bind(partial(set_scripts, poetry=poetry)),
        bind(partial(set_index_urls, poetry=poetry)),
        bind(partial(set_extra_sections, poetry=poetry)),
    )
    return result.alt(raise_exception).unwrap()
