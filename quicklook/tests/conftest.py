"""Shared fixtures: one generated universe and one processed workspace per test session.

Everything runs offline with the deterministic parser. Eval numbers are collected
in EVALS and written to measurements/evals.json at the end of the session.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from quicklook import pipeline, store, synth
from quicklook.config import ROOT, Workspace
from quicklook.parse import OfflineParser
from quicklook.reference import Reference

# Tests never send traces or call the model, even when keys sit in .env.
os.environ["QUICKLOOK_TRACING"] = "off"
os.environ["QUICKLOOK_OFFLINE"] = "1"

EVALS: dict = {}


@dataclass
class Env:
    data: Path
    ws: Workspace
    offers: Path
    labels: dict
    ref: Reference

    def offer_file(self, code: str) -> Path:
        files = {o["code"].lower(): o["file"] for o in self.labels["offers"]}
        return self.offers / files.get(code.lower(), f"{code.lower()}.eml")

    def con(self, read_only: bool = True):
        return store.connect(self.ws, read_only=read_only)


@pytest.fixture(scope="session")
def env(tmp_path_factory) -> Env:
    data = tmp_path_factory.mktemp("data")
    synth.run(data, seed=42)
    ws = Workspace(root=data / "main", reference_dir=data / "reference")
    parser = OfflineParser()
    for f in sorted((data / "offers").glob("*.eml")) + sorted((data / "offers" / "more").glob("*.eml")):
        pipeline.process_email(f.read_bytes(), ws, parser, channel="test")
    labels = json.loads((data / "fixtures" / "labels.json").read_text())
    return Env(data, ws, data / "offers", labels, Reference(data / "reference"))


@pytest.fixture
def fresh_ws(env, tmp_path) -> Workspace:
    return Workspace(root=tmp_path / "ws", reference_dir=env.data / "reference")


def pytest_sessionfinish(session, exitstatus):
    if EVALS:
        out = ROOT / "measurements"
        out.mkdir(exist_ok=True)
        (out / "evals.json").write_text(json.dumps(EVALS, indent=2, default=str))
