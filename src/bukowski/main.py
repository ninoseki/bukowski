import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import tomlkit
from packaging.utils import canonicalize_name
from poetry.core.constraints.version import (
    Version,
    VersionConstraint,
    VersionUnion,
    parse_constraint,
)
from poetry.core.packages.dependency import Dependency
from poetry.core.packages.path_dependency import PathDependency
from poetry.core.packages.url_dependency import URLDependency
from poetry.core.packages.vcs_dependency import VCSDependency
from poetry.core.poetry import Poetry
from returns.functions import raise_exception
from returns.pipeline import flow
from returns.pointfree import bind
from returns.result import ResultE, safe
from tomlkit.container import Container, OutOfOrderTableProxy
from tomlkit.exceptions import NonExistentKey
from tomlkit.items import AbstractTable, Array, SingleKey, Table
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


def get_dependency_groups(pyproject: TOMLDocument):
    dependency_groups: dict[str, Any] | None = pyproject.get("dependency-groups")
    if not dependency_groups:
        dependency_groups = tomlkit.table()
        pyproject["dependency-groups"] = dependency_groups

    return dependency_groups


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


def normalize_name(name: str):
    # NOTE: > names must start and end with a letter or digit and may only contain -, _, ., and alphanumeric characters.  # noqa: E501
    #       (ref, https://github.com/astral-sh/uv/blob/main/crates/uv-cli/src/lib.rs#L46-L53)
    subbed = re.sub("[^a-zA-Z0-9._-]+", "-", name)
    return canonicalize_name(subbed)


@safe
def set_project(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    package = poetry.package

    content = cast(dict[str, Any], pyproject["project"])

    content["name"] = normalize_name(package.name)
    content["version"] = package.version.text
    content["description"] = package.description

    # NOTE: a dirty hack to set readme
    #       (package.readme is a Path object and it's not suitable for serialization)
    readme = poetry.pyproject.data.get("tool", {}).get("poetry", {}).get("readme")
    if readme:
        content["readme"] = readme

    constraint = parse_constraint(poetry.package.python_versions)
    content["requires-python"] = str(constraint)

    if package.license:
        content["license"] = package.license.id

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

    dependencies = [
        dependency for dependency in group.dependencies if not dependency.is_optional()
    ]
    pep508_dependencies = [
        dependency_to_pep508(dependency) for dependency in dependencies
    ]

    content = cast(dict[str, Any], pyproject["project"])
    content["dependencies"] = strings_to_array(pep508_dependencies)

    return pyproject


def get_tool_uv_sources(pyproject: TOMLDocument) -> Table:
    uv = get_tool_uv(pyproject)
    sources = uv.get("sources")
    if not sources:
        sources = tomlkit.table()
        uv["sources"] = sources

    return sources


@safe
def set_sources(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    dependencies = [
        dependency
        for dependency in poetry.package.all_requires
        if isinstance(dependency, VCSDependency | URLDependency | PathDependency)
    ]
    if len(dependencies) == 0:
        return pyproject

    dependencies = sorted(dependencies, key=lambda d: d.pretty_name)

    sources = get_tool_uv_sources(pyproject)

    for dependency in dependencies:
        if isinstance(dependency, VCSDependency):
            vcs_sources = {
                dependency.vcs: dependency.source,
                "rev": dependency.rev,
                "tag": dependency.tag,
                "branch": dependency.branch,
            }
            filtered = {
                k: v for k, v in vcs_sources.items() if v is not None and str(v) != ""
            }
            inline_table = tomlkit.inline_table()
            inline_table.update(filtered)
            sources[dependency.pretty_name] = inline_table

        if isinstance(dependency, URLDependency):
            url_sources = {
                "url": dependency.source_url,
            }
            inline_table = tomlkit.inline_table()
            inline_table.update(url_sources)
            sources[dependency.pretty_name] = inline_table

        if isinstance(dependency, PathDependency):
            path_sources = {
                "path": str(dependency.path),
            }
            inline_table = tomlkit.inline_table()
            inline_table.update(path_sources)
            sources[dependency.pretty_name] = inline_table

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

    dependency_groups = get_dependency_groups(pyproject)
    dependency_groups["dev"] = strings_to_array(pep508_dependencies)

    return pyproject


@safe
def set_optional_dependencies(
    pyproject: TOMLDocument, *, poetry: Poetry
) -> TOMLDocument:
    if not poetry.package.extras:
        return pyproject

    content = cast(dict[str, Any], pyproject["project"])
    optional_dependencies = content.get("optional-dependencies")
    if not optional_dependencies:
        optional_dependencies = tomlkit.table()
        content["optional-dependencies"] = optional_dependencies

    # set optional extra dependencies
    for name, dependencies in poetry.package.extras.items():
        pep508_dependencies = [
            dependency_to_pep508(dependency) for dependency in dependencies
        ]
        optional_dependencies[name] = strings_to_array(pep508_dependencies)

    return pyproject


@safe
def set_dependency_groups(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    filtered = [
        (key, group)
        for key, group in poetry.package._dependency_groups.items()
        if key not in ["main", "dev"]
    ]
    if not filtered:
        return pyproject

    dependency_groups = get_dependency_groups(pyproject)

    # set non-extra & dev dependencies
    for key, group in filtered:
        pep508_dependencies = [
            dependency_to_pep508(dependency) for dependency in group.dependencies
        ]
        dependency_groups[key] = strings_to_array(pep508_dependencies)

    return pyproject


def remove_from_item_or_container(item_or_container: Any, key: str):
    # tomlkit may parse a table as OutOfOrderTableProxy
    # so we need to handle both cases
    if isinstance(item_or_container, OutOfOrderTableProxy):
        # modifying OutOfOrderTableProxy#__delitem__
        if (
            key not in item_or_container._tables_map
            or SingleKey(key) not in item_or_container._tables_map
        ):
            raise NonExistentKey(key)

        for i in reversed(item_or_container._tables_map[key]):
            table = cast(Table, item_or_container._tables[i])
            if key in table:
                del table[key]

            if not table and len(item_or_container._tables) > 1:
                item_or_container._remove_table(table)

        del item_or_container._tables_map[key]
        del item_or_container._internal_container[key]
        if key is not None:
            dict.__delitem__(item_or_container, key)

        return None

    if isinstance(item_or_container, AbstractTable | TOMLDocument | Container):
        return item_or_container.remove(key)

    raise ValueError(f"Unsupported type: {type(item_or_container)}")


def strip_double_newlines(t: Table) -> Table:
    """
    Super table can have trailing double newlines and it makes the output ugly.
    So it should be removed.
    """
    if not t.is_super_table():
        return t

    @safe
    def inner():
        string = t.as_string()
        if string.endswith("\n\n"):
            string = string[:-1]

        parsed = tomlkit.parse(string)
        # first item should be a table
        table = parsed._body[0][1]
        if not isinstance(table, Table):
            raise ValueError(f"Expected a table, but got {type(table)}")

        table.name = t.name
        return table

    return inner().value_or(t)


@safe
def set_extra_sections(pyproject: TOMLDocument, *, poetry: Poetry) -> TOMLDocument:
    # NOTE: use tomlkit to preserve comments and formatting
    with open(poetry.pyproject.path) as f:
        data = tomlkit.loads(f.read())

    if "build-system" in data:
        remove_from_item_or_container(data, "build-system")

    poetry_tool: AbstractTable | TOMLDocument | OutOfOrderTableProxy | None = data.pop(
        "tool"
    )
    if poetry_tool and "poetry" in poetry_tool:
        remove_from_item_or_container(poetry_tool, "poetry")

    for key, value in data.items():
        pyproject[key] = value

    if not poetry_tool:
        return pyproject

    tool = pyproject.get("tool")
    if not tool:
        tool = tomlkit.table()
        pyproject["tool"] = tool

    for key, value in poetry_tool.items():
        if isinstance(value, Table):
            tool[key] = strip_double_newlines(value)
        else:
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
        is_not_tagged: bool = set(source.keys()) == {"url", "name"}
        if (
            priority in ["default", "primary"] or default or is_not_tagged
        ) and not index_url:
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
        bind(partial(set_dependency_groups, poetry=poetry)),
        bind(partial(set_scripts, poetry=poetry)),
        bind(partial(set_index_urls, poetry=poetry)),
        bind(partial(set_sources, poetry=poetry)),
        bind(partial(set_extra_sections, poetry=poetry)),
    )
    return result.alt(raise_exception).unwrap()
