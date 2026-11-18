# Deploying the sample

Two containers: the app and a Caddy reverse proxy that terminates TLS and sets
`noindex` so the demo does not show up in search.

## Prerequisites

- A host with Docker and Docker Compose.
- A DNS A record for the demo subdomain pointing at the host.
- Ports 80 and 443 open.

Nothing in the demo needs outbound network access in cached mode. The app reads
only the seeded synthetic files in `data/`.

## Run it

From the repo root:

```bash
cp deploy/.env.example deploy/.env
# edit DEMO_DOMAIN
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Caddy gets a certificate on first request. Check it:

```bash
curl -sI https://$DEMO_DOMAIN | head -20
```

## Update

```bash
git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

## Logs and stop

```bash
docker compose -f deploy/docker-compose.yml logs -f web
docker compose -f deploy/docker-compose.yml down
```

## Live model mode (optional)

The demo defaults to cached deterministic outputs so a click never fails. To let
the app call Bedrock instead, set `SAVVY_LLM_MODE=bedrock`, `BEDROCK_MODEL_ID`,
and `AWS_REGION` in `deploy/.env`, and run the container with an instance role
that can call that one model. Do not put access keys in the file.

## Rules

- Synthetic data only. Do not point this at anything real.
- Keep `noindex` on. The demo is unlisted, not private.
- Remove the demo after the conversation with the company is over.
