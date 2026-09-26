"""ChiJa ↔ Hermes guided Device Pairing plugin."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

try:
    from . import connect as chija_connect_impl
except ImportError:  # loaded as a flat plugin directory
    import connect as chija_connect_impl  # type: ignore

logger = logging.getLogger(__name__)

PLUGIN_VERSION = chija_connect_impl.PLUGIN_VERSION
CONNECT_DOC = "https://app.chija.io/connect/hermes"
BOOTSTRAP_PATH = "/.well-known/chija-agent-bootstrap-hermes.json"


def _find_connector_bin() -> str | None:
    env = os.environ.get("HERMES_CHIJA_CONNECTOR_BIN", "").strip()
    if env and Path(env).is_file():
        return env
    which = shutil.which("hermes-channel-chija")
    if which:
        return which
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parents[1] / "tools" / "hermes-channel-chija" / "bin" / "hermes-channel-chija",
        here.parents[1] / "tools" / "hermes-channel-chija" / "bin" / "hermes-channel-chija.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _handle_chija_connect(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    base_url = str(params.get("base_url") or params.get("baseUrl") or "").strip().rstrip("/")
    if not base_url:
        return json.dumps(
            {
                "success": False,
                "error": "base_url required",
                "hint": f"Fetch {CONNECT_DOC} or bootstrap JSON, then retry.",
            },
            ensure_ascii=False,
        )

    gateway_id = str(
        params.get("gateway_instance_id") or params.get("gatewayInstanceId") or ""
    ).strip()
    profiles = params.get("profiles") or params.get("agents") or []
    if isinstance(profiles, str):
        profiles = [p.strip() for p in profiles.split(",") if p.strip()]
    device_label = str(params.get("device_label") or params.get("deviceLabel") or "").strip()
    timeout_seconds = int(params.get("timeout_seconds") or 600)
    messages: list[str] = []

    try:
        result = chija_connect_impl.run_connect(
            base_url=base_url,
            gateway_instance_id=gateway_id,
            profiles=[str(p) for p in profiles] if profiles else None,
            device_label=device_label,
            timeout_seconds=timeout_seconds,
            on_message=messages.append,
        )
    except Exception as err:  # noqa: BLE001 — surface to agent chat
        return json.dumps(
            {
                "success": False,
                "error": str(err),
                "messages": messages[-20:],
                "documentationUrl": CONNECT_DOC,
            },
            ensure_ascii=False,
        )

    connector = _find_connector_bin()
    next_step = result.get("next")
    if connector:
        next_step = f"{connector} run  (bindings already saved under {result.get('bindingsDir')})"

    return json.dumps(
        {
            "success": True,
            "messages": messages or result.get("messages") or [],
            "boundProfiles": result.get("boundProfiles"),
            "bindingsDir": result.get("bindingsDir"),
            "gatewayInstanceId": result.get("gatewayInstanceId"),
            "connectorBinary": connector,
            "next": next_step,
            "documentationUrl": CONNECT_DOC,
            "bootstrap": BOOTSTRAP_PATH,
        },
        ensure_ascii=False,
    )


def _handle_chija_status(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    connector = _find_connector_bin()
    root = chija_connect_impl.bindings_root()
    profiles_found: list[str] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "credentials.json").is_file():
                profiles_found.append(child.name)
    return json.dumps(
        {
            "success": True,
            "connectorBinary": connector,
            "bindingsDir": str(root),
            "boundProfiles": profiles_found,
            "documentationUrl": CONNECT_DOC,
        },
        ensure_ascii=False,
    )


CHIJA_CONNECT_SCHEMA = {
    "name": "chija_connect",
    "description": (
        "Connect Hermes profiles to ChiJa Agent Members via Device Authorization. "
        f"When the user shares {CONNECT_DOC} (or the bootstrap JSON), call this after "
        "installing the chija plugin. Shows a short userCode for ChiJa ADMIN approval; "
        "never ask the user for pairing codes or paste deviceCode/credentials into chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "ChiJa HTTPS API base URL (e.g. https://app.chija.io or https://api.chija.io)",
            },
            "gateway_instance_id": {
                "type": "string",
                "description": "Stable id for this Hermes workstation / gateway",
            },
            "profiles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of Hermes profile ids; default discovers all",
            },
            "device_label": {"type": "string", "description": "Optional human device label"},
            "timeout_seconds": {"type": "integer", "description": "Poll timeout (default 600)"},
        },
        "required": ["base_url"],
    },
}

CHIJA_STATUS_SCHEMA = {
    "name": "chija_status",
    "description": "Show ChiJa Hermes connector binary path and bound profiles.",
    "parameters": {"type": "object", "properties": {}},
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="chija_connect",
        toolset="chija",
        schema=CHIJA_CONNECT_SCHEMA,
        handler=_handle_chija_connect,
        description=CHIJA_CONNECT_SCHEMA["description"],
    )
    ctx.register_tool(
        name="chija_status",
        toolset="chija",
        schema=CHIJA_STATUS_SCHEMA,
        handler=_handle_chija_status,
        description=CHIJA_STATUS_SCHEMA["description"],
    )
    logger.info("ChiJa Hermes plugin registered (chija_connect, chija_status)")
