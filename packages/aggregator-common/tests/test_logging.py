import logging
from io import StringIO

import pytest

from aggregator_common.config import Settings
from aggregator_common.logging_setup import _HANDLER_ATTR, configure_logging


@pytest.fixture()
def settings():
    return Settings(database_url="postgresql+psycopg://localhost/test", log_level="DEBUG")


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Restore root logger state after each test to avoid cross-test pollution."""
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    yield
    root.setLevel(original_level)
    for handler in list(root.handlers):
        if handler not in original_handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in original_handlers:
        if handler not in root.handlers:
            root.addHandler(handler)


def _our_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, _HANDLER_ATTR, False)]


def test_sets_root_level_from_settings(settings):
    configure_logging(settings, stream=StringIO())
    assert logging.getLogger().level == logging.DEBUG


def test_installs_exactly_one_handler(settings):
    configure_logging(settings, stream=StringIO())
    assert len(_our_handlers(logging.getLogger())) == 1


def test_handler_targets_requested_stream(settings):
    stream = StringIO()
    configure_logging(settings, stream=stream)
    logging.getLogger("aggregator.test").info("sentinel-message")
    assert "sentinel-message" in stream.getvalue()


def test_idempotent_no_duplicate_handlers(settings):
    stream = StringIO()
    configure_logging(settings, stream=stream)
    configure_logging(settings, stream=stream)
    assert len(_our_handlers(logging.getLogger())) == 1


def test_default_stream_is_stdout(settings):
    import sys

    configure_logging(settings)
    handlers = _our_handlers(logging.getLogger())
    assert len(handlers) == 1
    assert isinstance(handlers[0], logging.StreamHandler)
    assert handlers[0].stream is sys.stdout
