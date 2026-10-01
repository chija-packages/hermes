"""Agent-principal file upload, attachment, and kanban card move for Hermes.

Uses the paired Agent Member token. Never returns or logs that token.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from . import connect as chija_connect_impl
except ImportError:  # flat plugin directory
    import connect as chija_connect_impl  # type: ignore


_SECRET = re.compile(r"(?i)chj_(agt|bot|pair)_[A-Za-z0-9._~+/=-]+")


def _redact(text: str) -> str:
    return _SECRET.sub(r"chj_\1_[REDACTED]", text)


def _require_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    parsed = urlparse(base)
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "https" or (parsed.scheme == "http" and loopback):
        return base
    raise RuntimeError("ChiJa base_url must be https (except loopback)")


def load_credential(profile: str | None = None) -> dict[str, str]:
    root = chija_connect_impl.bindings_root()
    if not root.is_dir():
        raise RuntimeError("ChiJa binding missing — run chija_connect first")
    wanted = (profile or os.environ.get("HERMES_PROFILE") or "").strip()
    matches: list[Path] = []
    for child in sorted(root.iterdir()):
        cred = child / "credentials.json"
        if child.is_dir() and cred.is_file():
            if not wanted or child.name == wanted:
                matches.append(cred)
    if not matches:
        raise RuntimeError("ChiJa binding missing — run chija_connect first")
    if wanted == "" and len(matches) > 1:
        raise RuntimeError("multiple ChiJa bindings; pass profile")
    raw = json.loads(matches[0].read_text(encoding="utf-8"))
    token = str(raw.get("token") or "")
    base = _require_base(str(raw.get("baseUrl") or ""))
    if not token.startswith("chj_agt_"):
        raise RuntimeError("ChiJa binding is incomplete")
    return {"baseUrl": base, "token": token}


def _api_json(cred: dict[str, str], method: str, path: str, body: dict[str, Any] | None,
              idempotency_key: str) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cred["baseUrl"] + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {cred['token']}",
            "Idempotency-Key": idempotency_key,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    return _read_json(req)


def _api_upload(cred: dict[str, str], path: str, filename: str, content: bytes,
                content_type: str, idempotency_key: str) -> dict[str, Any]:
    boundary = "chija" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        cred["baseUrl"] + path,
        data=head + content + tail,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {cred['token']}",
            "Idempotency-Key": idempotency_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    return _read_json(req)


def _read_json(req: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw = res.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(_redact(f"ChiJa HTTP {err.code}: {detail}")) from err
    except urllib.error.URLError as err:
        raise RuntimeError(_redact(f"ChiJa request failed: {err}")) from err
    parsed = json.loads(raw)
    if not parsed.get("success"):
        err = parsed.get("error") or {}
        raise RuntimeError(_redact(f"ChiJa {err.get('code')}: {err.get('message')}"))
    data = parsed.get("data")
    return data if isinstance(data, dict) else {}


def _absolute_file(file_path: str) -> Path:
    text = (file_path or "").strip()
    if not text or text.startswith("~"):
        raise RuntimeError("filePath must be an absolute path")
    path = Path(text)
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError("filePath must be an existing absolute file")
    return path


def upload_local_file(file_path: str, profile: str | None = None,
                      idempotency_key: str | None = None) -> dict[str, Any]:
    path = _absolute_file(file_path)
    cred = load_credential(profile)
    key = (idempotency_key or "").strip() or str(uuid.uuid4())
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    uploaded = _api_upload(
        cred, "/api/agent/v1/files", path.name, path.read_bytes(), content_type, key,
    )
    return {
        "ok": True,
        "fileId": uploaded.get("fileId"),
        "name": uploaded.get("name") or path.name,
        "contentType": uploaded.get("contentType") or content_type,
        "size": uploaded.get("size"),
        "idempotencyKey": key,
    }


def add_attachment(file_id: int, target_type: str, target_id: str,
                   profile: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    if file_id <= 0:
        raise RuntimeError("fileId is required")
    kind = (target_type or "").strip().upper()
    dest = (target_id or "").strip()
    if kind not in {"KANBAN_CARD", "CHAT_MESSAGE"} or not dest:
        raise RuntimeError("targetType must be KANBAN_CARD or CHAT_MESSAGE, with targetId")
    cred = load_credential(profile)
    key = (idempotency_key or "").strip() or str(uuid.uuid4())
    attached = _api_json(
        cred, "POST", "/api/agent/v1/attachments",
        {"fileId": file_id, "targetType": kind, "targetId": dest},
        key,
    )
    attached["ok"] = True
    attached["idempotencyKey"] = key
    return attached


def upload_and_attach(file_path: str, target_type: str, target_id: str,
                      profile: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    base = (idempotency_key or "").strip() or str(uuid.uuid4())
    uploaded = upload_local_file(file_path, profile, base + "-upload")
    attached = add_attachment(
        int(uploaded["fileId"]), target_type, target_id, profile, base + "-attach",
    )
    return {"ok": True, "upload": uploaded, "attachment": attached}


def create_card(board_id: int, column_id: int, title: str, description: str | None = None,
                profile: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    if board_id <= 0 or column_id <= 0:
        raise RuntimeError("boardId and columnId are required")
    text = (title or "").strip()
    if not text:
        raise RuntimeError("title is required")
    body: dict[str, Any] = {"columnId": column_id, "title": text}
    if description:
        body["description"] = description
    cred = load_credential(profile)
    key = (idempotency_key or "").strip() or str(uuid.uuid4())
    created = _api_json(
        cred, "POST", f"/api/agent/v1/boards/{board_id}/cards", body, key,
    )
    created["ok"] = True
    created["idempotencyKey"] = key
    return created


def move_card(card_id: int, column_id: int, target_board_id: int | None = None,
              profile: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    if card_id <= 0 or column_id <= 0:
        raise RuntimeError("cardId and columnId are required")
    body: dict[str, Any] = {"columnId": column_id, "placement": "COLUMN_TOP"}
    if target_board_id is not None and target_board_id > 0:
        body["targetBoardId"] = target_board_id
    cred = load_credential(profile)
    key = (idempotency_key or "").strip() or str(uuid.uuid4())
    moved = _api_json(
        cred, "POST", f"/api/agent/v1/board/cards/{card_id}/move", body, key,
    )
    moved["ok"] = True
    moved["idempotencyKey"] = key
    return moved
