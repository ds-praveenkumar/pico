"""Tests for rich logging capture mode used by the live dashboard."""

import logging
import time

from brain import logging_setup


def test_log_capture_drains_and_restores():
    logging_setup.setup_rich_logging()
    try:
        logging_setup.capture_logs(True)
        logging.getLogger("capture_test").info("hello capture")

        drained = []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            drained = logging_setup.drained_logs()
            if drained:
                break
            time.sleep(0.02)
        assert any("hello capture" in line for line in drained)

        logging_setup.capture_logs(False)
        logging.getLogger("capture_test").info("visible again")
        assert logging_setup.drained_logs() == []
    finally:
        logging_setup.stop_rich_logging()


def test_capture_without_listener_is_safe():
    logging_setup._listener = None
    logging_setup._capture = None
    logging_setup.capture_logs(True)
    logging_setup.capture_logs(False)
    assert logging_setup.drained_logs() == []