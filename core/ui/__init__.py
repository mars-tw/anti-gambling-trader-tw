"""Shared UI service package (stdlib only; no desktop/HTTP imports)."""

from core.ui.service import Artifact, UIError, UIService

__all__ = ["Artifact", "UIError", "UIService", "UIServer", "start_server"]


def __getattr__(name: str):
    """Keep ordinary service imports light while exposing the HTTP entry lazily."""

    if name in {"UIServer", "start_server"}:
        from core.ui.server import UIServer, start_server

        return {"UIServer": UIServer, "start_server": start_server}[name]
    raise AttributeError(name)
