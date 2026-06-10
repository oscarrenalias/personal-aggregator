import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aggregator_common.config import Settings

_HANDLER_ATTR = "_aggregator_handler"


def configure_logging(settings: "Settings", *, stream=None) -> None:
    """Configure the root logger from settings.log_level.

    Installs a single StreamHandler on the root logger writing to the given
    stream (default sys.stdout). Idempotent: a second call replaces the
    previously installed handler rather than adding a duplicate.
    """
    if stream is None:
        stream = sys.stdout

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()

    # Remove any handler we previously installed so repeated calls don't stack.
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    setattr(handler, _HANDLER_ATTR, True)

    root.setLevel(level)
    root.addHandler(handler)
