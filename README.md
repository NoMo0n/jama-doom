# DOOM in the Jama Connect OSLC window

A tiny OSLC provider whose delegated UI (the iframe Jama opens for selecting,
creating or viewing a linked artifact) is [webDOOM](https://github.com/UstymUkhman/webDOOM),
a WebAssembly build of PrBoom.

**Local testing only.** OAuth is accepted without checking anything, and every
response allows any origin to frame and fetch it. Do not expose it to a network.

## Run

```
docker compose up --build -d
```

- `https://localhost/` (Caddy, certificate from Caddy's local CA)
- `http://localhost:8080/` (straight to the provider, no TLS)

The first build downloads about 100 MB of game data from GitHub.

### Trust the Caddy certificate

Caddy runs in a container, so it cannot install its CA into Windows itself.
Until the CA is trusted, the browser blocks the iframe silently.

```
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt
certutil -addstore -user Root caddy-root.crt
```

Restart the browser afterwards. To undo it: `certutil -delstore -user Root "Caddy Local Authority"`
(check the exact name in `certmgr.msc` first).

## Point Jama at it

| What | URL |
| --- | --- |
| Root services | `https://localhost/rootservices` |
| Service provider catalog | `https://localhost/oslc/catalog` |
| Service provider | `https://localhost/oslc/provider` |
| Selection dialog | `https://localhost/oslc/dialog/select` |
| Creation dialog | `https://localhost/oslc/dialog/create` |
| Query base / creation factory | `https://localhost/oslc/runs` |
| OAuth 1.0a | `/oauth/request_token`, `/oauth/authorize`, `/oauth/access_token` |

Any consumer key and secret work. The provider advertises both the
Requirements Management and Architecture Management domains.

In the dialog, **Link this session** returns one artifact to Jama
(`https://localhost/oslc/runs/<id>`) and **Cancel** returns none. Opening a
linked artifact, or its OSLC large or small preview, loads the game again.

## Settings

Set these in a `.env` file next to `docker-compose.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HTTPS_PORT` | `443` | Host port for Caddy |
| `HTTP_PORT` | `8080` | Host port for the provider |
| `SITE_ADDRESS` | `localhost` | Hostname Caddy serves and issues a certificate for |
| `PUBLIC_URL` | (from request) | Fixes the base URL written into OSLC documents |
| `GAMES` | `doom1` | Build-time: `doom1`, `doom2` or `doom1 doom2` |

With both games built, add `?game=doom2` to a dialog URL to pick the second.

## Layout

- `provider/server.py`: the OSLC provider and static file server (Python standard library)
- `provider/play.html`: the page shown in the iframe; loads the game and answers the OSLC dialog
- `Dockerfile`: fetches webDOOM's prebuilt files at a pinned commit, then builds the provider image
- `Caddyfile`, `docker-compose.yml`: HTTPS reverse proxy on localhost

## Licensing

The engine is PrBoom (GPL) and webDOOM's own code is MIT; both licence files
are copied into the image under `/app/game`. The game data files in the
webDOOM repository belong to id Software. This image downloads them at build
time for personal testing; do not redistribute the built image.
