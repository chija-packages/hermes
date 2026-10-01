"""ChiJa ↔ Hermes guided Device Pairing plugin."""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    from . import connect as chija_connect_impl
    from . import files as chija_files_impl
    from . import supervisor as supervisor_impl
except ImportError:  # loaded as a flat plugin directory
    import connect as chija_connect_impl  # type: ignore
    import files as chija_files_impl  # type: ignore
    import supervisor as supervisor_impl  # type: ignore

logger = logging.getLogger(__name__)

PLUGIN_VERSION = chija_connect_impl.PLUGIN_VERSION
CONNECT_DOC = "https://app.chija.io/connect/hermes"
BOOTSTRAP_PATH = "/.well-known/chija-agent-bootstrap-hermes.json"


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
    timeout_seconds = int(params.get("timeout_seconds") or 1200)
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

    supervise: dict[str, Any]
    try:
        supervise = supervisor_impl.ensure_connector_running(download_bin=True)
        if supervise.get("started"):
            messages.append(
                f"ChiJa WSS connector started (pid={supervise.get('pid')}). "
                "Agent Member should become ONLINE shortly."
            )
        elif supervise.get("reason") == "already_running":
            messages.append("ChiJa WSS connector already running.")
        else:
            messages.append(f"ChiJa WSS connector: {supervise.get('reason')}")
    except Exception as err:  # noqa: BLE001
        supervise = {"started": False, "error": str(err)}
        messages.append(
            f"ChiJa WSS connector auto-start failed: {err}. "
            "Install binary via packages/hermes/install_connector.sh or set HERMES_CHIJA_CONNECTOR_BIN."
        )

    status = supervisor_impl.connector_status()
    return json.dumps(
        {
            "success": True,
            "messages": messages or result.get("messages") or [],
            "boundProfiles": result.get("boundProfiles"),
            "bindingsDir": result.get("bindingsDir"),
            "gatewayInstanceId": result.get("gatewayInstanceId"),
            "connectorBinary": status.get("binary"),
            "connector": status,
            "supervise": supervise,
            "next": (
                "WSS auto-started. Confirm ACTIVE · ONLINE on ChiJa Agent Members. "
                "Do not ask for email/password in chat."
            ),
            "documentationUrl": CONNECT_DOC,
            "bootstrap": BOOTSTRAP_PATH,
        },
        ensure_ascii=False,
    )


def _handle_chija_status(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    root = chija_connect_impl.bindings_root()
    profiles_found: list[str] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "credentials.json").is_file():
                profiles_found.append(child.name)
    status = supervisor_impl.connector_status()
    return json.dumps(
        {
            "success": True,
            "connector": status,
            "connectorBinary": status.get("binary"),
            "connectorRunning": status.get("running"),
            "bindingsDir": str(root),
            "boundProfiles": profiles_found,
            "documentationUrl": CONNECT_DOC,
        },
        ensure_ascii=False,
    )


CHIJA_CONNECT_SCHEMA = {
    "name": "chija_connect",
    "description": (
        "Connect Hermes profiles to ChiJa Agent Members via Device Authorization, then "
        "auto-start the ChiJa WSS sidecar (hermes-channel-chija) so the member becomes ONLINE. "
        f"When the user shares {CONNECT_DOC} (or bootstrap JSON), install the plugin then call this. "
        "Deliver ONLY the short userCode and the full approval URL as plain text to the user. "
        "Do NOT call, fetch, curl, open, or browse that URL yourself. "
        "NEVER ask for ChiJa email, password, or Google login in chat — the human signs in "
        "in their own browser. "
        "Never paste deviceCode or chj_agt_* tokens into chat."
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
            "timeout_seconds": {"type": "integer", "description": "Poll timeout (default 1200; covers 15m device-auth TTL)"},
        },
        "required": ["base_url"],
    },
}

CHIJA_STATUS_SCHEMA = {
    "name": "chija_status",
    "description": (
        "Show ChiJa Hermes bindings, WSS connector binary path, and whether the sidecar is running."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def _tool_json(fn, params: dict[str, Any]) -> str:
    try:
        return json.dumps(fn(params), ensure_ascii=False)
    except Exception as err:  # noqa: BLE001 — tool result, never a traceback with secrets
        return json.dumps({"ok": False, "error": str(err)}, ensure_ascii=False)


def _handle_file_upload(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    return _tool_json(
        lambda p: chija_files_impl.upload_local_file(
            str(p.get("filePath") or p.get("file_path") or ""),
            str(p.get("profile") or "") or None,
            str(p.get("idempotencyKey") or p.get("idempotency_key") or "") or None,
        ),
        params,
    )


def _handle_attachment_add(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    return _tool_json(
        lambda p: chija_files_impl.add_attachment(
            int(p.get("fileId") or p.get("file_id") or 0),
            str(p.get("targetType") or p.get("target_type") or ""),
            str(p.get("targetId") or p.get("target_id") or ""),
            str(p.get("profile") or "") or None,
            str(p.get("idempotencyKey") or p.get("idempotency_key") or "") or None,
        ),
        params,
    )


def _handle_file_attach(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    return _tool_json(
        lambda p: chija_files_impl.upload_and_attach(
            str(p.get("filePath") or p.get("file_path") or ""),
            str(p.get("targetType") or p.get("target_type") or ""),
            str(p.get("targetId") or p.get("target_id") or ""),
            str(p.get("profile") or "") or None,
            str(p.get("idempotencyKey") or p.get("idempotency_key") or "") or None,
        ),
        params,
    )


def _handle_board_card_create(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    return _tool_json(
        lambda p: chija_files_impl.create_card(
            int(p.get("boardId") or p.get("board_id") or 0),
            int(p.get("columnId") or p.get("column_id") or 0),
            str(p.get("title") or ""),
            str(p.get("description") or "") or None,
            str(p.get("profile") or "") or None,
            str(p.get("idempotencyKey") or p.get("idempotency_key") or "") or None,
        ),
        params,
    )


def _handle_board_card_move(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs
    board = params.get("targetBoardId", params.get("target_board_id", params.get("boardId")))
    return _tool_json(
        lambda p: chija_files_impl.move_card(
            int(p.get("cardId") or p.get("card_id") or 0),
            int(p.get("columnId") or p.get("column_id") or 0),
            int(board) if board not in (None, "") else None,
            str(p.get("profile") or "") or None,
            str(p.get("idempotencyKey") or p.get("idempotency_key") or "") or None,
        ),
        params,
    )


CHIJA_FILE_UPLOAD_SCHEMA = {
    "name": "chija_file_upload",
    "description": (
        "Upload a local file into the ChiJa workspace as this Agent Member. "
        "Returns fileId. Use an absolute path. Does not attach the file by itself. "
        "Upload only the finished file. Do not upload drafts, empty files, or intermediate revisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filePath": {"type": "string", "description": "Absolute local path"},
            "profile": {"type": "string", "description": "Hermes profile when several bindings exist"},
            "idempotencyKey": {"type": "string"},
        },
        "required": ["filePath"],
    },
}

CHIJA_ATTACHMENT_ADD_SCHEMA = {
    "name": "chija_attachment_add",
    "description": (
        "Attach an uploaded ChiJa file to a kanban card or chat message. "
        "targetType is KANBAN_CARD or CHAT_MESSAGE. Requires EDIT on that space."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fileId": {"type": "integer"},
            "targetType": {"type": "string"},
            "targetId": {"type": "string"},
            "profile": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        "required": ["fileId", "targetType", "targetId"],
    },
}

CHIJA_FILE_ATTACH_SCHEMA = {
    "name": "chija_file_attach",
    "description": (
        "Upload a local file and attach it in one step. "
        "Call it once with the finished file. Do not upload drafts, empty files, or intermediate revisions. "
        "Kanban: targetType=KANBAN_CARD and targetId=card id. "
        "Chat: targetType=CHAT_MESSAGE and targetId=message id. "
        "Use this when a person would attach a file. Do not use bot tokens or curl."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filePath": {"type": "string"},
            "targetType": {"type": "string"},
            "targetId": {"type": "string"},
            "profile": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        "required": ["filePath", "targetType", "targetId"],
    },
}

CHIJA_BOARD_CARD_CREATE_SCHEMA = {
    "name": "chija_board_card_create",
    "description": (
        "Create a kanban card the way a member with EDIT can. "
        "boardId is the board, columnId is the column, title is required. "
        "Creating a card does not start Agent work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "boardId": {"type": "integer"},
            "columnId": {"type": "integer"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "profile": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        "required": ["boardId", "columnId", "title"],
    },
}


CHIJA_BOARD_CARD_MOVE_SCHEMA = {
    "name": "chija_board_card_move",
    "description": (
        "Move a kanban card the way a member with EDIT can. "
        "columnId is the destination column. "
        "Set targetBoardId only to move the card onto another board in the same workspace; "
        "EDIT is required on both boards. Use this when a person must approve or decide, "
        "and that work lives on another board or column."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "cardId": {"type": "integer"},
            "columnId": {"type": "integer"},
            "targetBoardId": {"type": "integer"},
            "profile": {"type": "string"},
            "idempotencyKey": {"type": "string"},
        },
        "required": ["cardId", "columnId"],
    },
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
    for schema, handler in (
        (CHIJA_FILE_UPLOAD_SCHEMA, _handle_file_upload),
        (CHIJA_ATTACHMENT_ADD_SCHEMA, _handle_attachment_add),
        (CHIJA_FILE_ATTACH_SCHEMA, _handle_file_attach),
        (CHIJA_BOARD_CARD_CREATE_SCHEMA, _handle_board_card_create),
        (CHIJA_BOARD_CARD_MOVE_SCHEMA, _handle_board_card_move),
    ):
        ctx.register_tool(
            name=schema["name"],
            toolset="chija",
            schema=schema,
            handler=handler,
            description=schema["description"],
        )

    used_bg = supervisor_impl.try_register_background_service(ctx)
    if supervisor_impl.has_bindings():
        try:
            result = supervisor_impl.ensure_connector_running(download_bin=True)
            logger.info("ChiJa connector supervise on register: %s", result.get("reason"))
        except Exception as err:  # noqa: BLE001
            logger.warning("ChiJa connector auto-start on register failed: %s", err)

    logger.info(
        "ChiJa Hermes plugin registered "
        "(chija_connect, chija_status, chija_file_attach, chija_board_card_create, chija_board_card_move, bg_service=%s)",
        used_bg,
    )
