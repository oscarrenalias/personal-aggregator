import argparse
import logging

logger = logging.getLogger(__name__)


def run() -> None:
    parser = argparse.ArgumentParser(description="aggregator-retriever: RSS/Atom feed poller")
    parser.parse_args()
    logger.info("aggregator-retriever starting")
