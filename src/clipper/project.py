from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import yaml

from . import countries, paths
from .models import Project

FREQUENCIES = ("daily", "weekly", "monthly")
OPTIONAL = ("estimate", "caps")
LIST_FIELDS = ("subject", "exclude_terms", "exclude_domains", "programmes", "spokespeople", "languages",
               "social", "own_accounts", "parent_names")
SOCIAL_PLATFORMS = ("x", "linkedin", "instagram", "facebook", "tiktok", "youtube", "reddit")
DICT_FIELDS = ("contexts", "competitors", "report_groups", "aliases", "watch_accounts")


class ProjectError(ValueError):
    pass


def _project_file(folder: Path) -> Path:
    return Path(folder) / "project.yaml"


def load_project(folder: Path) -> Project:
    folder = Path(folder)
    f = _project_file(folder)
    if not f.is_file():
        raise ProjectError(f"project file not found: {f}")
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ProjectError(f"invalid YAML in {f}: {e}") from e
    if not isinstance(raw, dict):
        raise ProjectError(f"{f} must contain a mapping")

    kwargs: dict = {}
    for fld in fields(Project):
        if fld.name == "folder":
            continue
        if fld.name in OPTIONAL:
            kwargs[fld.name] = raw.get(fld.name)
        elif fld.name in raw:
            kwargs[fld.name] = raw[fld.name]
        elif fld.name in LIST_FIELDS:
            kwargs[fld.name] = []
        elif fld.name in DICT_FIELDS:
            kwargs[fld.name] = {}
        elif fld.name == "outlets_file":
            kwargs[fld.name] = ""
        else:
            raise ProjectError(f"missing field: {fld.name}")
    for name in LIST_FIELDS:
        kwargs[name] = kwargs[name] or []
    for name in DICT_FIELDS:
        kwargs[name] = kwargs[name] or {}
    kwargs["outlets_file"] = kwargs["outlets_file"] or ""
    kwargs["folder"] = folder

    if not kwargs["subject"]:
        raise ProjectError("subject must list at least one name to monitor")
    if not countries.is_known(str(kwargs["country"])):
        raise ProjectError(
            f"country must be a two-letter ISO code such as FR or KE, got {kwargs['country']!r}")
    kwargs["country"] = str(kwargs["country"]).upper()
    unknown = [s for s in kwargs["social"] if s not in SOCIAL_PLATFORMS]
    if unknown:
        raise ProjectError(f"social must use {', '.join(SOCIAL_PLATFORMS)}; got {unknown}")
    if not kwargs["languages"]:
        raise ProjectError("languages must list at least one language code, such as fr")
    if kwargs["frequency"] not in FREQUENCIES:
        raise ProjectError(
            f"frequency must be one of {', '.join(FREQUENCIES)}, got {kwargs['frequency']!r}"
        )
    return Project(**kwargs)


def save_project(p: Project) -> None:
    data = {}
    for fld in fields(Project):
        if fld.name == "folder":
            continue
        value = getattr(p, fld.name)
        if fld.name in OPTIONAL and value is None:
            continue
        data[fld.name] = value
    p.folder.mkdir(parents=True, exist_ok=True)
    _project_file(p.folder).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _registry_file() -> Path:
    return paths.home() / "projects.yaml"


def list_projects() -> dict[str, Path]:
    f = _registry_file()
    if not f.is_file():
        return {}
    raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {name: Path(folder) for name, folder in raw.items()}


def register_project(p: Project) -> None:
    projects = list_projects()
    projects[p.name] = p.folder
    _registry_file().write_text(
        yaml.safe_dump({k: str(v) for k, v in projects.items()}, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )


def find_project(name: str) -> Path:
    projects = list_projects()
    if name not in projects:
        raise ProjectError(f"unknown project: {name!r}")
    return projects[name]
