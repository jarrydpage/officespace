from __future__ import annotations

from datetime import datetime
import logging
import sys
from typing import TextIO


class FractionalSecondFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created)
        if datefmt:
            return timestamp.strftime(datefmt)
        return timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")


def configure_logging(
    *,
    stream: TextIO | None = None,
    level: int = logging.INFO,
) -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        FractionalSecondFormatter(
            fmt="[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S.%f",
        )
    )
    root_logger.setLevel(level)
    root_logger.addHandler(handler)