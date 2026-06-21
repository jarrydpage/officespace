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

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        lines = rendered.splitlines()
        if len(lines) <= 1:
            return rendered

        prefix_record = logging.makeLogRecord(record.__dict__.copy())
        prefix_record.msg = ""
        prefix_record.args = ()
        prefix_record.message = ""
        if self.usesTime():
            prefix_record.asctime = self.formatTime(record, self.datefmt)
        prefix = self.formatMessage(prefix_record)

        prefixed_lines = [lines[0]]
        prefixed_lines.extend(f"{prefix}{line}" for line in lines[1:])
        return "\n".join(prefixed_lines)


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