# ChiJa Hermes Plugin

Guided Device Pairing for Hermes Agent — OpenClaw-like UX (auto WSS after pair).

**Canonical public install repo:** https://github.com/chija-packages/hermes  
(Source of truth in the ChiJa monorepo is `packages/hermes`; publish by copying plugin files to the public repo root.)

## Install

Ops publishes the current pin:

- Human: https://app.chija.io/connect/hermes
- Machine: https://app.chija.io/.well-known/chija-agent-bootstrap-hermes.json

```bash
hermes plugins install chija-packages/hermes --enable
# WSS sidecar binary (once per machine; also auto-downloaded by the plugin if missing):
bash <(curl -fsSL https://raw.githubusercontent.com/chija-packages/hermes/main/install_connector.sh) v0.2.6
```

Or let `chija_connect` download `hermes-channel-chija` from GitHub Releases into `~/.config/chija/hermes-channel/bin/`.

## After pairing

Plugin auto-starts `hermes-channel-chija run` (pid file + log under `~/.config/chija/hermes-channel/`).  
No manual `run` — confirm **ACTIVE · ONLINE** on ChiJa Agent Members.

## Develop locally

```bash
hermes plugins install /absolute/path/to/ChiJa/packages/hermes --enable
cd tools/hermes-channel-chija && go build -o ./bin/hermes-channel-chija ./cmd/hermes-channel-chija
export HERMES_CHIJA_CONNECTOR_BIN="$PWD/bin/hermes-channel-chija"
```

## Tools

| Tool | Role |
|------|------|
| `chija_connect` | Device Auth → save bindings → auto-start WSS sidecar |
| `chija_status` | Bindings + connector binary + running pid |

See `docs/ops/chija-agent-connector-releases.md`.
