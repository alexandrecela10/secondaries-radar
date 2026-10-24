"""Paths and run settings. One place, no magic."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("QUICKLOOK_DATA", ROOT / "data"))

RULEBOOK_PATH = CONFIG_DIR / "rulebook.yaml"
PLAN_PATH = CONFIG_DIR / "plan.yaml"

# Synthetic "today" for the demo. All offers are dated around this.
DEFAULT_REFERENCE_DATE = "2026-06-30"


@dataclass(frozen=True)
class Workspace:
    """Where one pipeline instance reads reference data and writes its layers.

    Two intake paths in the architecture experiment use two workspaces with the
    same reference data, so their outputs can be compared byte for byte.
    """

    root: Path
    reference_dir: Path

    @property
    def db_path(self) -> Path:
        return self.root / "quicklook.duckdb"

    @property
    def bronze_dir(self) -> Path:
        return self.root / "bronze"

    @property
    def briefs_dir(self) -> Path:
        return self.root / "briefs"

    def ensure(self) -> "Workspace":
        for p in (self.root, self.bronze_dir, self.briefs_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self


def default_workspace(name: str = "main") -> Workspace:
    return Workspace(root=DATA_DIR / name, reference_dir=DATA_DIR / "reference")


def offers_dir() -> Path:
    return DATA_DIR / "offers"


def fixtures_path() -> Path:
    return DATA_DIR / "fixtures" / "labels.json"
