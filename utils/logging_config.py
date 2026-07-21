"""Configuration centralisée du logging structuré.

Remplace progressivement les print() dispersés par des logs à niveaux,
contrôlables via la variable d'environnement LOG_LEVEL (DEBUG/INFO/WARNING/ERROR).

Usage :
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("message")
"""

import logging
import os
import sys

_CONFIGURED = False


def setup_logging(level: str = None) -> None:
    """
    Configure le root logger une seule fois (format + niveau).
    Idempotent : un second appel ne duplique pas les handlers.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))

    root = logging.getLogger()
    root.setLevel(log_level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    else:
        # Réaligne le format des handlers existants (ex: lastResort)
        for h in root.handlers:
            h.setFormatter(handler.formatter)

    # Calmer les libs tierces bavardes : httpx logue CHAQUE requête HTTP à INFO
    # (un appel LLM/embedding = une ligne), ce qui noie nos logs métier. On les
    # remonte à WARNING (sauf si on est déjà en DEBUG, pour le diagnostic réseau).
    if log_level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "sentence_transformers", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger nommé (configure le logging au besoin)."""
    setup_logging()
    return logging.getLogger(name)
