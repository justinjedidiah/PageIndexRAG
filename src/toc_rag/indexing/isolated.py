"""Read a document in a separate process, with a time limit.

A crafted PDF (one page holding millions of drawn shapes, say) can stay under the page and size caps
and still take many minutes to read. The web app reads only a couple of documents at once, so two such
uploads would make every other visitor wait. A thread cannot be stopped from outside; a process can.

Everything a read produces besides the index already goes to disk (images, a PDF's saved read), so the
child process only has to send the index back. Reading in another process also means a read never
holds this process's PDF lock, so page pictures stay quick while a document is being read.
"""

import multiprocessing
from collections.abc import Callable
from multiprocessing.context import BaseContext
from pathlib import Path
from typing import Any, TypeVar

from ..config import Settings
from ..models import DocumentIndex, IndexOptions
from ..storage import Store
from .blocks import DocumentRejected
from .pipeline import build_index

T = TypeVar("T")
_context: BaseContext | None = None


class ReadTimedOut(DocumentRejected):
    """The document took longer to read than the limit allows. The message is safe to show."""


def build_index_isolated(path: Path, store: Store, settings: Settings, options: IndexOptions | None = None) -> DocumentIndex:
    """`build_index` in a child process, stopped after `settings.read_timeout_seconds`."""
    return run_isolated(build_index, (path, store, settings, options), settings.read_timeout_seconds)


def run_isolated(fn: Callable[..., T], args: tuple, timeout: float) -> T:
    """Run a top-level function in a child process and return its result, or raise what it raised.

    Raises ReadTimedOut if it is not done within `timeout` seconds (the child is killed), and
    RuntimeError if the child dies without answering (for example, out of memory).
    """
    context = _get_context()
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(sender, fn, args), daemon=True)
    process.start()
    sender.close()  # the child has its own copy; closing ours lets us notice if the child dies
    try:
        if not receiver.poll(timeout):
            minutes = timeout / 60
            limit = f"{minutes:g} minute{'s' if minutes != 1 else ''}" if timeout >= 60 else f"{timeout:g} seconds"
            raise ReadTimedOut(
                f"This document took too long to read (the limit is {limit}). "
                "Its pages may be unusually complex; try a simpler or shorter version."
            )
        status, value = receiver.recv()
    except EOFError:
        raise RuntimeError("The reading process stopped without an answer.") from None
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join(5)
    if status == "error":
        raise value
    return value


def _child(sender: Any, fn: Callable[..., Any], args: tuple) -> None:
    try:
        result = ("ok", fn(*args))
    except BaseException as exc:  # sent back and raised in the parent
        result = ("error", exc)
    try:
        sender.send(result)
    except Exception:  # the result or the exception could not be pickled
        sender.send(("error", RuntimeError(f"{type(result[1]).__name__} in the reading process")))
    finally:
        sender.close()


def _get_context() -> BaseContext:
    """How child processes are started. Never "fork": the web server runs threads, and a forked copy can
    inherit a lock that another thread was holding at that moment, and hang on it."""
    global _context
    if _context is None:
        if "forkserver" in multiprocessing.get_all_start_methods():  # Linux, macOS
            context = multiprocessing.get_context("forkserver")
            # Imported once by the fork server, so each read starts in milliseconds, not seconds.
            context.set_forkserver_preload(["toc_rag.indexing.pipeline"])
            _context = context
        else:  # Windows: each read starts a fresh interpreter (a second or two), fine for dev
            _context = multiprocessing.get_context("spawn")
    return _context
