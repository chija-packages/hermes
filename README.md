# ChiJa Hermes Plugin

Guided Device Pairing for Hermes Agent — analogous to `@chija/openclaw`.

**Canonical public install repo:** https://github.com/chija-packages/hermes  
(Source of truth in the ChiJa monorepo is still `packages/hermes`; publish by copying this directory to the public repo root.)

## Install

Ops publishes the current pin (version / installCommand):

- Human: https://app.chija.io/connect/hermes
- Machine: https://app.chija.io/.well-known/chija-agent-bootstrap-hermes.json
- API: `GET /api/v1/public/agent-connectors/hermes`

```bash
hermes plugins install chija-packages/hermes --enable
```

## Develop locally

```bash
hermes plugins install /absolute/path/to/ChiJa/packages/hermes --enable
```

## Tools

| Tool | Role |
|------|------|
| `chija_connect` | Discover profiles → Device Auth → save bindings |
| `chija_status` | Show connector binary + bound profiles |

See `docs/ops/chija-agent-connector-releases.md`.
