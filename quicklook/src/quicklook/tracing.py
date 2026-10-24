"""Langfuse tracing: one trace per offer run, scores on it, team feedback as scores.

Without LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY every call here is a silent
no-op, so the pipeline and the tests run offline. Key values are never printed.

Trace "quick-look-offer" (session = the email's Message-ID)
  intake (span)       bronze write or duplicate
  parse (generation when the Claude parser runs: model, prompt version, tokens; else span)
  match (retriever)   our funds looked up by description and ratios
  metrics (span)
  verdict (span)      rulebook result
  brief (span)
  validator (guardrail)
Scores on the trace: validator_pass, data_matched, verdict_band.
Feedback scores, added later by `radar keep | override | resolve`: team_decision,
verdict_agrees, team_label, line_resolved.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from .config import ROOT

TRACE_NAME = "quick-look-offer"
_env_loaded = False
_tracer: "Tracer | None" = None


def load_env() -> None:
    """Read quicklook/.env, then the repo root .env. Variables already set win."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    for p in (ROOT / ".env", ROOT.parent / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


class _Noop:
    """Stands in for a Langfuse observation when tracing is off."""

    trace_id = None
    id = None

    def update(self, **_: Any) -> "_Noop":
        return self

    def score(self, **_: Any) -> None:
        pass

    def score_trace(self, **_: Any) -> None:
        pass


NOOP = _Noop()


class Tracer:
    """Thin facade over the Langfuse client. `client=None` means off."""

    def __init__(self, client: Any = None):
        self.client = client

    @property
    def on(self) -> bool:
        return self.client is not None

    def _in_trace(self) -> bool:
        return self.client.get_current_trace_id() is not None

    @contextmanager
    def run(self, name: str, *, input: Any, session_id: str | None = None, metadata: dict | None = None,
            tags: list[str] | None = None) -> Iterator[Any]:
        """Root observation of one offer run. Inside an experiment item it nests under the item."""
        if not self.on:
            yield NOOP
            return
        from langfuse import propagate_attributes

        with self.client.start_as_current_observation(as_type="span", name=name, input=input) as root:
            with propagate_attributes(session_id=session_id, metadata=metadata, tags=tags, trace_name=name):
                yield root

    @contextmanager
    def step(self, name: str, as_type: str = "span", **kw: Any) -> Iterator[Any]:
        """A child step. Outside a run (an override re-rendering a brief) it records nothing."""
        if not self.on or not self._in_trace():
            yield NOOP
            return
        with self.client.start_as_current_observation(name=name, as_type=as_type, **kw) as obs:
            yield obs

    def feedback(self, trace_id: str | None, name: str, value: Any, data_type: str, comment: str | None = None,
                 score_id: str | None = None, metadata: dict | None = None) -> None:
        """A team signal on an earlier trace (keep, override, resolve)."""
        if not self.on or not trace_id:
            return
        self.client.create_score(name=name, value=value, trace_id=trace_id, data_type=data_type, comment=comment,
                                 score_id=score_id, metadata=metadata)

    def flush(self) -> None:
        if self.on:
            self.client.flush()


def _export(span) -> bool:
    """Default Langfuse filter, minus the Anthropic SDK's own HTTP span. Our 'parse' generation
    already carries model, tokens and latency; keeping both would show the call twice."""
    from langfuse.span_filter import is_default_export_span

    scope = getattr(span, "instrumentation_scope", None)
    return is_default_export_span(span) and not (scope and scope.name == "com.anthropic.sdk.python")


def keys_present() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def get_tracer() -> Tracer:
    """The process-wide tracer. Off when keys are missing or QUICKLOOK_TRACING=off."""
    global _tracer
    if _tracer is not None:
        return _tracer
    if os.environ.get("QUICKLOOK_TRACING", "").lower() == "off" or not keys_present():
        _tracer = Tracer(None)
        return _tracer
    try:
        from langfuse import Langfuse
        kw = {} if os.environ.get("LANGFUSE_TRACING_ENVIRONMENT") else {"environment": "demo"}
        _tracer = Tracer(Langfuse(should_export_span=_export, **kw))
    except Exception as e:  # never block the pipeline on tracing
        print(f"Langfuse tracing off: {type(e).__name__}.", file=sys.stderr)
        _tracer = Tracer(None)
    return _tracer


def set_tracer(t: Tracer | None) -> None:
    """For tests: install a tracer (or reset to lazy init with None)."""
    global _tracer
    _tracer = t
