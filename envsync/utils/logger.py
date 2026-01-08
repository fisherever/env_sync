import logging
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


def get_logger(name: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        console = Console()
        handler = RichHandler(console=console, rich_tracebacks=True, show_time=False)
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
