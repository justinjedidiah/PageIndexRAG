"""Keep secrets out of logs, whatever library writes them.

The app never logs a visitor's key itself, but libraries can: Reflex logs the raw event data in some
warnings (a "save key" event carries the key), and an access log line holds the URL, whose query has
Reflex's client token (`/_event/?token=...`), which works like a login. `install()` changes how every
log record is made, so each one is cleaned before any handler (console, file, host) sees it.
"""

import logging
import re

_PATTERNS = [
    # Provider keys: Anthropic (sk-ant-...), OpenAI (sk-..., sk-proj-...), Google (AIza...). Only as a word of
    # their own (\b): "risk-assessment-report-3fa9c1d2" or "task-list" are document ids, not keys.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "AIza***"),
    # Reflex's client token in a URL query or a header.
    (re.compile(r"(\btoken=)[^&\s\"']+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(reflex-client-token['\"]?\s*[:,]\s*['\"]?)[^\s'\",}]+", re.IGNORECASE), r"\1***"),
]
_FORMATTER = logging.Formatter()
_installed = False


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def install() -> None:
    """Clean every log record from now on. Safe to call more than once."""
    global _installed
    if _installed:
        return
    make_record = logging.getLogRecordFactory()

    def clean_record(*args, **kwargs) -> logging.LogRecord:
        record = make_record(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # bad format arguments: leave the record as it is, logging reports it
            return record
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        if record.exc_info:  # a traceback's text could hold one too: format it now, cleaned
            record.exc_text = redact(_FORMATTER.formatException(record.exc_info))
        return record

    logging.setLogRecordFactory(clean_record)
    _installed = True
