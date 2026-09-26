"""Pure-Python ChiJa Device Authorization for Hermes guided connect."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

PLUGIN_VERSION = "0.2.1"
CAPABILITIES = [
    "invocation.accept",
    "run.progress",
    "run.input_required",
    "run.cancel",
    "session.resume",
]


def bindings_root() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config / "chija" / "hermes-channel" / "bindings"


def discover_profiles(hermes_bin: str = "hermes") -> list[str]:
    try:
        out = subprocess.check_output(
            [hermes_bin, "profile", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        ids: list[str] = []
        seen: set[str] = set()
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            token = line.split()[0].strip("│|")
            if token in {"NAME", "Profile", "---", ""}:
                continue
            if token not in seen:
                seen.add(token)
                ids.append(token)
        if ids:
            return ids
    except (OSError, subprocess.SubprocessError):
        pass

    root = Path.home() / ".hermes"
    ids = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            ids.append(name)

    if _is_hermes_home(root):
        add("default")
    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and _is_hermes_home(child):
                add(child.name)
    if not ids:
        raise RuntimeError(
            f"no Hermes profiles found under {root}; create one then retry chija_connect"
        )
    return ids


def _is_hermes_home(path: Path) -> bool:
    for name in ("config.yaml", ".env", "SOUL.md", "profile.yaml", "auth.json", "state.db"):
        if (path / name).is_file():
            return True
    return False


def _api_post(base_url: str, path: str, body: dict[str, Any], token: str = "") -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ChiJa HTTP {err.code}") from exc
        message = (parsed.get("error") or {}).get("message") or f"ChiJa HTTP {err.code}"
        code = (parsed.get("error") or {}).get("code") or str(err.code)
        raise RuntimeError(f"ChiJa {code}: {message}") from err

    parsed = json.loads(raw)
    if not parsed.get("success"):
        err = parsed.get("error") or {}
        raise RuntimeError(f"ChiJa {err.get('code')}: {err.get('message')}")
    return parsed.get("data") or {}


def _save_credentials(path: Path, credentials: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def run_connect(
    *,
    base_url: str,
    gateway_instance_id: str = "",
    profiles: list[str] | None = None,
    device_label: str = "",
    timeout_seconds: int = 600,
    on_message: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Start Device Auth, wait for ADMIN approval, save per-profile credentials.

    Never returns or logs deviceCode / raw tokens in messages.
    """
    log = on_message or (lambda _m: None)
    base = base_url.rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.") and "localhost" not in base:
        # Allow http only for loopback; otherwise require https.
        if base.startswith("http://"):
            raise RuntimeError("ChiJa base_url must be https (except loopback)")

    discovered = discover_profiles()
    selected = list(profiles) if profiles else list(discovered)
    if not selected:
        raise RuntimeError("no Hermes profiles selected")
    for profile in selected:
        if profile not in discovered:
            raise RuntimeError(
                f"AGENT_NOT_FOUND: {profile!r} not in Hermes profile list ({', '.join(discovered)})"
            )

    gateway_id = gateway_instance_id.strip() or f"hermes-{socket.gethostname()}"
    body: dict[str, Any] = {
        "adapterType": "NATIVE_CHANNEL",
        "runtimeType": "HERMES",
        "gatewayInstanceId": gateway_id,
        "pluginVersion": PLUGIN_VERSION,
        "requestedAgents": [
            {"externalAgentId": p, "displayName": p} for p in selected
        ],
        "requestedCapabilities": CAPABILITIES,
    }
    if device_label:
        body["deviceLabel"] = device_label
    created = _api_post(
        base,
        "/api/agent/v1/device-authorizations",
        body,
    )
    user_code = created.get("userCode") or ""
    verification = created.get("verificationUri") or ""
    expires_in = int(created.get("expiresIn") or 180)
    interval = max(1, int(created.get("interval") or 5))
    device_code = created.get("deviceCode") or ""
    if not user_code or not verification or not device_code:
        raise RuntimeError("device authorization response incomplete")

    log(f"ChiJa 연결 승인 코드: {user_code}")
    log(f"승인 페이지(사람이 직접 열 것): {verification}?userCode={user_code}")
    log(f"만료: 약 {expires_in}초 · Profiles: {', '.join(selected)}")
    log(
        "IMPORTANT: 위 URL을 Agent/브라우저 도구로 열지 마세요. "
        "대화창에 코드와 URL 텍스트만 보여 주고, 사람이 자기 브라우저에서 "
        "ChiJa 로그인·ADMIN 승인할 때까지 기다리세요. "
        "이메일·비밀번호·Google 인증은 채팅으로 묻지 마세요."
    )

    deadline = time.time() + min(timeout_seconds, expires_in + 30)
    approved: dict[str, Any] | None = None
    while time.time() < deadline:
        token_body = _api_post(
            base,
            "/api/agent/v1/device-authorizations/token",
            {"deviceCode": device_code, "gatewayInstanceId": gateway_id},
        )
        if token_body.get("status") == "approved":
            approved = token_body
            break
        err = token_body.get("error") or "authorization_pending"
        wait = int(token_body.get("interval") or interval)
        if err == "slow_down":
            wait = min(wait * 2, 30)
        log(f"대기 중 ({err}, {wait}s)…")
        time.sleep(wait)
    device_code = ""
    del device_code

    if not approved:
        raise RuntimeError("device authorization expired or was not approved in time")

    agents = approved.get("agents") or []
    workspace_id = approved.get("workspaceId")
    ok = fail = 0
    bound: list[str] = []
    root = bindings_root()
    for agent in agents:
        profile = str(agent.get("externalAgentId") or "")
        raw_token = str(agent.get("rawToken") or "")
        if not profile or not raw_token.startswith("chj_agt_"):
            fail += 1
            log(f"연결 실패 profile={profile or '?'}: invalid credential")
            continue
        cred = {
            "baseUrl": base,
            "token": raw_token,
            "workspaceId": workspace_id,
            "agentMemberId": agent.get("agentMemberId"),
            "connectorId": agent.get("connectorId"),
        }
        dir_path = root / _safe_name(profile)
        _save_credentials(dir_path / "credentials.json", cred)
        (dir_path / "binding.json").write_text(
            json.dumps(
                {
                    "gatewayInstanceId": gateway_id,
                    "externalAgentId": profile,
                    "hermesProfile": profile,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(dir_path / "binding.json", 0o600)
        try:
            _api_post(
                base,
                "/api/agent/v1/openclaw-binding/live-verify",
                {
                    "gatewayInstanceId": gateway_id,
                    "externalAgentId": profile,
                    "expectLive": True,
                },
                token=raw_token,
            )
        except RuntimeError:
            pass
        log(
            f"연결 완료 profile={profile} member={agent.get('agentMemberId')} "
            f"connector={agent.get('connectorId')}"
        )
        bound.append(profile)
        ok += 1

    log(f"요약: 성공 {ok} · 실패 {fail}")
    if ok == 0:
        raise RuntimeError("no bindings saved")
    return {
        "success": True,
        "gatewayInstanceId": gateway_id,
        "boundProfiles": bound,
        "bindingsDir": str(root),
        "messages": [
            f"ChiJa 연결 승인 코드: {user_code}",
            f"승인 페이지: {verification}?userCode={user_code}",
            f"요약: 성공 {ok} · 실패 {fail}",
        ],
        "next": (
            "ChiJa WSS connector will auto-start (OpenClaw-like). "
            "Confirm ACTIVE · ONLINE on Agent Members. "
            "Do not ask for email/password in chat."
        ),
    }


def _safe_name(value: str) -> str:
    out = []
    for ch in value:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    return "".join(out) or "profile"
