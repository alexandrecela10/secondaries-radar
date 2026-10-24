"""Classifier port. Every adapter returns the same schema-checked Classification."""
from __future__ import annotations

from typing import Protocol

from ..models import Classification


class ClassifierError(RuntimeError):
    """The adapter could not return a valid Classification. Routed to the dead-letter topic."""


class Classifier(Protocol):
    name: str
    version: str

    def classify(self, headline: str, pension_name: str | None) -> Classification: ...


def get_classifier(prefer: str = "auto") -> Classifier:
    """auto: Claude when ANTHROPIC_API_KEY is set, else the offline keyword adapter (PRD C-4)."""
    import os

    from .keyword import KeywordClassifier

    if prefer == "keyword" or (prefer == "auto" and not os.environ.get("ANTHROPIC_API_KEY")):
        return KeywordClassifier()
    from .claude import ClaudeClassifier

    return ClaudeClassifier()
