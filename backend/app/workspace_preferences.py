"""Small owner-scoped UI preferences; no jobs, license state or database changes."""
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from .sources.registry import is_known_source, source_catalog


class WorkspaceRoute(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    source: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    view: Literal["projects", "project", "favorites", "car", "scans", "source-settings", "settings", "admin"]
    projectId: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    carId: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)

    @field_validator("source")
    @classmethod
    def registered_source(cls, value):
        if not is_known_source(value):
            raise ValueError("workspace_source_unknown")
        return value

    @model_validator(mode="after")
    def valid_ids(self):
        if self.view == "project" and self.projectId is None or self.view == "car" and self.carId is None:
            raise ValueError("workspace_route_missing_id")
        if self.view not in {"project", "car"} and self.projectId is not None or self.view != "car" and self.carId is not None:
            raise ValueError("workspace_route_unexpected_id")
        return self


class WorkspacePreferences(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    current: WorkspaceRoute
    sites: dict[str, WorkspaceRoute]

    @model_validator(mode="after")
    def valid_sites(self):
        for source, route in self.sites.items():
            if route.source != source or route.view in {"settings", "admin"}:
                raise ValueError("workspace_site_mismatch")
        return self


def workspace_path(user_id: int) -> Path:
    root = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    if not root:
        raise ValueError("desktop_workspace_unavailable")
    return Path(root) / "ui" / f"workspace-{user_id}.json"


def load_workspace(user_id: int) -> str | None:
    path = workspace_path(user_id)
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * (len(source_catalog()) + 1):
            raise ValueError("workspace_file_invalid")
        return WorkspacePreferences.model_validate_json(path.read_text(encoding="utf-8")).model_dump_json()
    except FileNotFoundError:
        return None
    except (OSError, ValueError, ValidationError):
        logging.getLogger(__name__).warning("Desktop navigation preferences could not be read")
        return None


def save_workspace(user_id: int, preferences: WorkspacePreferences) -> None:
    path = workspace_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".workspace-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(preferences.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
