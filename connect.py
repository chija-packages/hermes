"""Pure-Python ChiJa Device Authorization for Hermes guided connect."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

PLUGIN_VERSION = "0.2.10"
CAPABILITIES = [
    "invocation.accept",
    "run.progress",
    "run.input_required",
    "run.cancel",
    "session.resume",
]

# Hermes `profile list` may prefix the active row with ◆ / box-drawing / bullets.
_PROFILE_MARKERS = frozenset("◆◇●○■□▪▫*•·►▶→✓✔✖✗⚠⚠️★☆+")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HEADER_TOKENS = frozenset(
    {
        "NAME",
        "Name",
        "Profile",
        "Profiles",
        "PROFILE",
        "Active",
        "STATUS",
        "Status",
        "---",
    }
)


def bindings_root() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return config / "chija" / "hermes-channel" / "bindings"


def normalize_profile_token(raw: str) -> str | None:
    """Turn a CLI table cell into a Hermes profile id, or None if not a profile."""
    token = (raw or "").strip().strip("│|┃▌▐")
    if not token:
        return None
    while token and (token[0] in _PROFILE_MARKERS or not token[0].isalnum()):
        # Strip markers / punctuation prefixes (e.g. ◆default → default)
        if token[0].isalnum():
            break
        token = token[1:].lstrip()
    if not token or token in _HEADER_TOKENS:
        return None
    if set(token) <= set("-─━═|_= "):
        return None
    # Warning / prose lines often start with non-id words
    if not _PROFILE_NAME_RE.fullmatch(token):
        return None
    return token


_NOISE_TOKENS = frozenset(
    {
        "Restart",
        "Warning",
        "Warn",
        "Note",
        "Error",
        "Failed",
        "Success",
        "Gateway",
        "gateway",
        "plugin",
        "Plugin",
        "changes",
        "after",
        "Please",
        "please",
    }
)


def parse_profile_list_output(out: str) -> list[str]:
    """Parse `hermes profile list` stdout into profile ids (order preserved)."""
    ids: list[str] = []
    seen: set[str] = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip warning / instruction prose (e.g. "⚠ Restart gateway…")
        if line[0] in "⚠⚠️!" or line.lower().startswith(("warning", "error", "note:")):
            continue
        # Prefer first cell; also accept "◆ default" / "◆default  (active)"
        parts = line.replace("\t", " ").split()
        if not parts:
            continue
        # Long prose rows are not profile tables
        if len(parts) >= 4 and not any(p.startswith("◆") or p.startswith("│") for p in parts[:2]):
            continue
        candidates = [parts[0]]
        if len(parts) >= 2 and not parts[0][-1:].isalnum():
            candidates.append(parts[0] + parts[1])
            candidates.append(parts[1])
        for cand in candidates:
            name = normalize_profile_token(cand)
            if name and name not in _NOISE_TOKENS and name not in seen:
                seen.add(name)
                ids.append(name)
                break
    return ids


def discover_profiles_from_fs(root: Path | None = None) -> list[str]:
    """Discover profiles from ~/.hermes (+ profiles/*). Stable source of truth."""
    root = root if root is not None else (Path.home() / ".hermes")
    ids: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen and _PROFILE_NAME_RE.fullmatch(name):
            seen.add(name)
            ids.append(name)

    if _is_hermes_home(root):
        add("default")
    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and _is_hermes_home(child):
                add(child.name)
    return ids


def discover_profiles(hermes_bin: str = "hermes") -> list[str]:
    """Discover Hermes profile ids.

    Filesystem under ~/.hermes is preferred (stable). CLI output is merged after
    normalizing markers like ◆default → default; invalid rows are dropped.
    """
    from_fs = discover_profiles_from_fs()
    from_cli: list[str] = []
    try:
        out = subprocess.check_output(
            [hermes_bin, "profile", "list"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        from_cli = parse_profile_list_output(out)
    except (OSError, subprocess.SubprocessError):
        pass

    ids: list[str] = []
    seen: set[str] = set()

    def add_all(names: list[str]) -> None:
        for name in names:
            if name not in seen:
                seen.add(name)
                ids.append(name)

    # FS first so default/profiles win over noisy CLI chrome.
    add_all(from_fs)
    add_all(from_cli)
    if not ids:
        raise RuntimeError(
            "no Hermes profiles found under ~/.hermes; create one then retry chija_connect"
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
            if err.code == 429:
                raise RuntimeError(
                    "ChiJa 429: Too Many Requests. Do NOT retry or bypass with curl/scripts. "
                    "Wait 15–20 minutes, then call chija_connect once."
                ) from exc
            raise RuntimeError(f"ChiJa HTTP {err.code}") from exc
        message = (parsed.get("error") or {}).get("message") or f"ChiJa HTTP {err.code}"
        code = (parsed.get("error") or {}).get("code") or str(err.code)
        if err.code == 429 or str(code) in {"429", "AGENT_DEVICE_AUTHORIZATION_RATE_LIMITED"}:
            raise RuntimeError(
                f"ChiJa {code}: {message}. Do NOT retry or bypass with curl/scripts. "
                "Wait 15–20 minutes, then call chija_connect once."
            ) from err
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
    timeout_seconds: int = 1_200,
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
    expires_in = int(created.get("expiresIn") or 900)
    interval = max(1, int(created.get("interval") or 5))
    device_code = created.get("deviceCode") or ""
    if not user_code or not verification or not device_code:
        raise RuntimeError("device authorization response incomplete")

    log(f"ChiJa 연결 승인 코드: {user_code}")
    log(f"승인 페이지(사람이 직접 열 것): {verification}?userCode={user_code}")
    log(f"만료: 약 {expires_in}초 · Profiles: {', '.join(selected)}")
    log(
        "IMPORTANT: 위 승인 URL을 직접 호출하거나 열지 마세요. "
        "curl, fetch, 브라우저 도구를 쓰지 마세요. "
        "승인 코드와 URL 텍스트만 사용자에게 전달하고, 사용자가 자기 브라우저에서 "
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
