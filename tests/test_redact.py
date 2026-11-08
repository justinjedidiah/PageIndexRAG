import logging

from toc_rag import redact


def test_keys_and_tokens_are_masked():
    text = (
        "event {'api_key': 'sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv'} openai sk-proj-1234567890abcdefXYZ "
        "google AIzaSyA1234567890abcdefghijklmnop GET /_event/?token=3f2c9a1e-5b7d&EIO=4 "
        "headers {'reflex-client-token': '3f2c9a1e-5b7d'}"
    )
    cleaned = redact.redact(text)
    for secret in ("AbCdEfGh", "1234567890abcdefXYZ", "AIzaSyA1234", "3f2c9a1e"):
        assert secret not in cleaned
    assert "token=***&EIO=4" in cleaned  # the rest of the line stays readable


def test_ordinary_text_is_untouched():
    text = "Indexing failed: RateLimitError; sk-short; ask-anything; tokens=1200"
    assert redact.redact(text) == text


def test_installed_factory_cleans_every_logger(caplog):
    redact.install()
    redact.install()  # twice is harmless
    with caplog.at_level(logging.WARNING):
        logging.getLogger("reflex.app").warning("Event data: %s", {"api_key": "sk-ant-api03-SecretSecretSecret12"})
        try:
            raise RuntimeError("bad key sk-ant-api03-InTracebackInTraceback")
        except RuntimeError:
            logging.getLogger("some.library").exception("failed")
    assert "SecretSecret" not in caplog.text
    assert "InTraceback" not in caplog.text
    assert "sk-***" in caplog.text


def test_document_ids_containing_sk_are_not_masked():
    # Bug: "risk-", "task-", "desk-" contain "sk-", so document ids were masked as if they were keys.
    text = "Indexed risk-assessment-report-3fa9c1d2, task-management-handbook-0011aa22 and desk-booking-guide-99aa88bb"
    assert redact.redact(text) == text
    # Keys still are, wherever they stand on their own.
    for line in ("key='sk-ant-api03-AbCdEfGhIjKlMnOp'", "Bearer sk-proj-AbCdEfGhIjKlMnOpQr", "x-api-key: sk-AbCdEfGhIjKlMnOpQrSt"):
        assert "AbCdEfGh" not in redact.redact(line)
