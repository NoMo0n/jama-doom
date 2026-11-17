# syntax=docker/dockerfile:1

# ---- Stage 1: fetch the prebuilt webDOOM WebAssembly bundle -----------------
FROM alpine:3.20 AS assets
RUN apk add --no-cache git
# Pinned so the image is reproducible; override with --build-arg to move it.
ARG WEBDOOM_REPO=https://github.com/UstymUkhman/webDOOM
ARG WEBDOOM_REF=d859ca74907f8306c4f17a22e1d38980ea864ecc
RUN git init -q /src \
 && cd /src \
 && git remote add origin "$WEBDOOM_REPO" \
 && git fetch -q --depth 1 origin "$WEBDOOM_REF" \
 && git checkout -q FETCH_HEAD
# Space-separated list: "doom1", "doom2" or "doom1 doom2".
ARG GAMES="doom1"
RUN mkdir /game \
 && for g in $GAMES; do cp "/src/public/$g.js" "/src/public/$g.wasm" "/src/public/$g.data" /game/; done \
 && cp /src/LICENSE /game/LICENSE.webdoom.txt \
 && cp /src/COPYING /game/COPYING.prboom.txt

# ---- Stage 2: the OSLC provider (Python standard library only) --------------
FROM python:3.12-alpine
WORKDIR /app
COPY provider/ /app/
COPY --from=assets /game /app/game
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
USER nobody
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["python", "server.py"]
