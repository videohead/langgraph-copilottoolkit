import atexit
import logging
import os
from typing import Any

_LOG = logging.getLogger(__name__)

_CHECKPOINTER: Any | None = None
_CHECKPOINTER_CTX: Any | None = None
_CHECKPOINTER_READY = False


def _env_flag(name: str, default: str = "true") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _close_checkpointer() -> None:
    global _CHECKPOINTER, _CHECKPOINTER_CTX
    if _CHECKPOINTER_CTX is None:
        return
    try:
        _CHECKPOINTER_CTX.__exit__(None, None, None)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Failed to close Postgres checkpointer cleanly: %s", exc)
    finally:
        _CHECKPOINTER = None
        _CHECKPOINTER_CTX = None


def get_checkpointer() -> Any | None:
    """Create and cache a Postgres-backed LangGraph checkpointer.

    Returns None when persistence is disabled or unavailable.
    """
    global _CHECKPOINTER, _CHECKPOINTER_CTX, _CHECKPOINTER_READY

    if _CHECKPOINTER_READY:
        return _CHECKPOINTER

    _CHECKPOINTER_READY = True

    if not _env_flag("LANGGRAPH_CHECKPOINTS_ENABLED", "true"):
        _LOG.info("LangGraph checkpoints disabled via LANGGRAPH_CHECKPOINTS_ENABLED")
        return None

    if _env_flag("LANGGRAPH_API_MODE", "false"):
        _LOG.info(
            "LangGraph custom checkpointer used only during checkpointer initialization - disabled in API mode; runtime-managed persistence is used"
        )
        return None

    postgres_uri = os.environ.get("POSTGRES_URI", "").strip()
    if not postgres_uri:
        _LOG.info("LangGraph checkpoints disabled: POSTGRES_URI is not set")
        return None

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Postgres checkpoint backend unavailable: %s", exc)
        return None

    try:
        ctx = PostgresSaver.from_conn_string(postgres_uri)
        checkpointer = ctx.__enter__()

        if _env_flag("LANGGRAPH_CHECKPOINTS_AUTO_SETUP", "true") and hasattr(checkpointer, "setup"):
            checkpointer.setup()

        _CHECKPOINTER_CTX = ctx
        _CHECKPOINTER = checkpointer
        atexit.register(_close_checkpointer)
        _LOG.info("LangGraph Postgres checkpointer enabled")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("Failed to initialize Postgres checkpointer: %s", exc)
        _CHECKPOINTER = None
        _CHECKPOINTER_CTX = None

    return _CHECKPOINTER
