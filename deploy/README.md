# Deploying the suite

Two containers: the app and a Caddy reverse proxy that terminates TLS and sets
`noindex` so the demo does not show up in search.

## Prerequisites

- A host with Docker and Docker Compose.
- A DNS A record for the demo subdomain pointing at the host.
- Ports 80 and 443 open.

The recordings and the synthetic data are baked into the image, so the demo
works without any network call. Live model mode is optional.

## Run it

From the repo root:

```bash
cp deploy/.env.example deploy/.env
# edit DEMO_DOMAIN, and set DEEPSEEK_API_KEY if you want live model calls
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Caddy gets a certificate on first request. Check it:

```bash
curl -sI https://$DEMO_DOMAIN | head -20
```

## Model mode

The suite ships with recorded live calls in `data/cached/llm/`. Those replay
with no key and no network. Setting `SAVVY_LLM_MODE=live` and a
`DEEPSEEK_API_KEY` lets the pages make fresh calls, capped by
`SAVVY_LLM_DAILY_LIMIT` per day across all samples.

The key is passed through the environment at runtime. It is never written into
the image or the repository.

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

## Rules

- Synthetic data only. Do not point this at anything real.
- Keep `noindex` on. The demo is unlisted, not private.
- Remove the demo after the conversation with the company is over.
