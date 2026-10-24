"""Langfuse tracing for the batch run, plus user-feedback scores.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (shell, firstcall/.env or the repo .env).
When off, every call here is a silent no-op and the langfuse package is never imported, so tests run
offline. Key values are never printed or logged.

Trace shape for one batch run (names are stable; filters and evaluators depend on them):

  run-batch                      span       input: sources and queries; output: counts and verdicts
    fetch-source                 retriever  one per source and query; output: item count or error
    dedupe-items                 span       items in, unique links out
    classify-headline            generation (Claude: model, prompt version, tokens) or span (keyword rules)
    score-pensions               span       output: score and verdict per touched pension
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parent.parent
_loaded = False


def load_env() -> None:
    """Read firstcall/.env, then the repo .env. Existing environment variables win. Set FC_NO_DOTENV=1 to skip."""
    global _loaded
    if _loaded or os.environ.get("FC_NO_DOTENV") == "1":
        return
    _loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:          # pragma: no cover
        return
    for p in (_ROOT / ".env", _ROOT.parent / ".env"):
        if p.exists():
            load_dotenv(p, override=False)
    # SDK v5 reads LANGFUSE_BASE_URL; older tools read LANGFUSE_HOST. Keep both in step.
    if os.environ.get("LANGFUSE_HOST") and not os.environ.get("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_BASE_URL"] = os.environ["LANGFUSE_HOST"]
    if os.environ.get("LANGFUSE_BASE_URL") and not os.environ.get("LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]


def enabled() -> bool:
    load_env()
    return (os.environ.get("FC_TRACING", "1") != "0"
            and bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(os.environ.get("LANGFUSE_SECRET_KEY")))


class _NoOp:
    """Stands in for a Langfuse observation when tracing is off."""

    id = None
    trace_id = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, **kw: Any) -> "_NoOp":
        return self

    def score(self, **kw: Any) -> None:
        return None


NOOP = _NoOp()


def client():
    if not enabled():
        return None
    from langfuse import get_client     # imported only after the env is loaded

    return get_client()


@contextmanager
def observe(name: str, as_type: str = "span", **kw: Any) -> Iterator[Any]:
    """A child of the current observation. Without an active trace (stream path, replays, tests) it is a
    no-op, so only the batch run produces traces."""
    lf = client()
    if lf is None or not _in_trace():
        yield NOOP
        return
    with lf.start_as_current_observation(name=name, as_type=as_type, **kw) as obs:
        yield obs


@contextmanager
def run_trace(name: str, *, tags: list[str], metadata: dict[str, str], **kw: Any) -> Iterator[Any]:
    """Root observation with trace attributes (tags, metadata, environment) propagated to its children."""
    lf = client()
    if lf is None:
        yield NOOP
        return
    from langfuse import propagate_attributes

    with lf.start_as_current_observation(name=name, as_type="span", **kw) as root:
        with propagate_attributes(trace_name=name, tags=tags, metadata=metadata,
                                  version=metadata.get("rulebook_version")):
            yield root


def _in_trace() -> bool:
    from opentelemetry import trace

    return trace.get_current_span().get_span_context().is_valid


def current_trace_id() -> str | None:
    lf = client()
    return lf.get_current_trace_id() if lf and _in_trace() else None


def score_trace(trace_id: str | None, name: str, value: Any, *, data_type: str, comment: str | None = None,
                metadata: dict | None = None) -> bool:
    """Attach a score to a trace (user feedback). Returns False when tracing is off or no trace is known."""
    lf = client()
    if lf is None or not trace_id:
        return False
    lf.create_score(name=name, value=value, trace_id=trace_id, data_type=data_type, comment=comment,
                    metadata=metadata)
    return True


def flush() -> None:
    lf = client()
    if lf is not None:
        lf.flush()
