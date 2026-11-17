"""Minimal OSLC provider whose delegated UI is a game of DOOM.

Local testing only: OAuth 1.0a is accepted without validating anything.
Python standard library only.
"""

import json
import os
import re
import secrets
import shutil
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
GAME_DIR = Path(os.environ.get("GAME_DIR", HERE / "game")).resolve()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
DEFAULT_GAME = os.environ.get("DEFAULT_GAME", "doom1")

PLAY_HTML = (HERE / "play.html").read_text(encoding="utf-8")

RDF_NS = (
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:oslc="http://open-services.net/ns/core#" '
    'xmlns:oslc_rm="http://open-services.net/ns/rm#" '
    'xmlns:oslc_am="http://open-services.net/ns/am#"'
)
RM = "http://open-services.net/ns/rm#"
AM = "http://open-services.net/ns/am#"

STATIC_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".wasm": "application/wasm",
    ".data": "application/octet-stream",
    ".txt": "text/plain; charset=utf-8",
}

ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<rect width="16" height="16" fill="#111"/>'
    '<path d="M3 3h5a5 5 0 0 1 0 10H3z" fill="#c22"/>'
    '<path d="M6 6h2a2 2 0 0 1 0 4H6z" fill="#111"/></svg>'
)

# Sample artifacts returned by the query capability, so list-style consumers
# have something to show.
SAMPLE_RUNS = {
    "e1m1": "E1M1: Hangar",
    "e1m2": "E1M2: Nuclear Plant",
    "e1m3": "E1M3: Toxin Refinery",
}

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def run_title(run_id):
    return SAMPLE_RUNS.get(run_id, "DOOM session " + run_id)


def available_games():
    return sorted(p.stem for p in GAME_DIR.glob("*.wasm"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DoomOSLC/1.0"

    # ---- plumbing -------------------------------------------------------

    def base(self):
        if PUBLIC_URL:
            return PUBLIC_URL
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
                or "localhost:%d" % PORT).split(",")[0].strip()
        return "%s://%s" % (proto, host)

    def common_headers(self):
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        if origin:
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            self.headers.get("Access-Control-Request-Headers")
            or "Accept, Authorization, Content-Type, OSLC-Core-Version",
        )
        # Lets an https page on the public internet frame/fetch localhost.
        self.send_header("Access-Control-Allow-Private-Network", "true")
        # Deliberately no X-Frame-Options or CSP frame-ancestors: being framed
        # by any page is the whole point.
        self.send_header("OSLC-Core-Version", "2.0")

    def send(self, status, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.common_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def rdf(self, inner, status=200, headers=None, ctype="application/rdf+xml"):
        body = '<?xml version="1.0" encoding="UTF-8"?>\n<rdf:RDF %s>\n%s\n</rdf:RDF>\n' % (RDF_NS, inner)
        self.send(status, body, ctype + "; charset=utf-8", headers)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- routing --------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self.common_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_PUT(self):
        self.read_body()
        self.route()

    def do_POST(self):
        self.route()

    def do_GET(self):
        self.route()

    def route(self):
        url = urlsplit(self.path)
        path = url.path.rstrip("/") or "/"
        query = parse_qs(url.query)
        post = self.command == "POST"

        if path.startswith("/game/"):
            return self.static(path[len("/game/"):])
        if path == "/icon.svg":
            return self.send(200, ICON_SVG, "image/svg+xml", {"Cache-Control": "max-age=86400"})
        if path == "/healthz":
            return self.send(200, "ok")

        if path.startswith("/oauth/"):
            return self.oauth(path, query)

        if post and path != "/oslc/runs":
            self.read_body()

        if path in ("/", "/play"):
            # One URL for everything: a browser or iframe gets the game, an
            # OSLC client asking for RDF gets the discovery document.
            accept = self.headers.get("Accept", "").lower()
            if path == "/" and "text/html" not in accept and ("rdf" in accept or "xml" in accept):
                return self.rootservices()
            return self.play("view", query)
        if path in ("/rootservices", "/oslc/rootservices"):
            return self.rootservices()
        if path == "/oslc/catalog":
            return self.catalog()
        if path == "/oslc/provider":
            return self.provider()
        if path == "/oslc/dialog/select":
            return self.play("select", query)
        if path == "/oslc/dialog/create":
            return self.play("create", query)
        if path == "/oslc/runs":
            return self.create_run() if post else self.query_runs()
        if path.startswith("/oslc/runs/"):
            run_id = path[len("/oslc/runs/"):]
            if RUN_ID.match(run_id):
                return self.run(run_id, query)
        self.send(404, "not found")

    # ---- game -----------------------------------------------------------

    def static(self, name):
        target = (GAME_DIR / name).resolve()
        if GAME_DIR not in target.parents or not target.is_file():
            return self.send(404, "not found")
        size = target.stat().st_size
        self.send_response(200)
        self.common_headers()
        self.send_header("Content-Type", STATIC_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=604800, immutable")
        self.end_headers()
        if self.command != "HEAD":
            with target.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)

    def play(self, mode, query, run_id=None):
        games = available_games()
        game = (query.get("game") or [DEFAULT_GAME])[0]
        if game not in games:
            game = DEFAULT_GAME if DEFAULT_GAME in games else (games[0] if games else "")
        config = {
            "mode": mode,
            "game": game,
            "base": self.base(),
            "runId": run_id,
            "title": run_title(run_id) if run_id else None,
        }
        # "</" must not appear inside the inline <script>.
        blob = json.dumps(config).replace("</", "<\\/")
        self.send(200, PLAY_HTML.replace("__CONFIG__", blob), "text/html; charset=utf-8")

    # ---- OSLC discovery -------------------------------------------------

    def rootservices(self):
        b = self.base()
        body = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:Description rdf:about="{b}/rootservices"
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:dc="http://purl.org/dc/terms/"
    xmlns:jfs="http://jazz.net/xmlns/prod/jazz/jfs/1.0/"
    xmlns:jd="http://jazz.net/xmlns/prod/jazz/discovery/1.0/"
    xmlns:oslc_rm="http://open-services.net/xmlns/rm/1.0/"
    xmlns:oslc_cm="http://open-services.net/xmlns/cm/1.0/"
    xmlns:oslc_qm="http://open-services.net/xmlns/qm/1.0/"
    xmlns:oslc_am="http://open-services.net/ns/am#">
  <dc:title>DOOM OSLC Provider</dc:title>
  <oslc_rm:rmServiceProviders rdf:resource="{b}/oslc/catalog"/>
  <oslc_am:amServiceProviders rdf:resource="{b}/oslc/catalog"/>
  <oslc_cm:cmServiceProviders rdf:resource="{b}/oslc/catalog"/>
  <oslc_qm:qmServiceProviders rdf:resource="{b}/oslc/catalog"/>
  <jd:oslcCatalogs>
    <oslc:ServiceProviderCatalog xmlns:oslc="http://open-services.net/ns/core#" rdf:about="{b}/oslc/catalog">
      <oslc:domain rdf:resource="{rm}"/>
      <oslc:domain rdf:resource="{am}"/>
    </oslc:ServiceProviderCatalog>
  </jd:oslcCatalogs>
  <jfs:oauthRealmName>DOOM</jfs:oauthRealmName>
  <jfs:oauthDomain>{b}</jfs:oauthDomain>
  <jfs:oauthRequestConsumerKeyUrl rdf:resource="{b}/oauth/request_key"/>
  <jfs:oauthApprovalModuleUrl rdf:resource="{b}/oauth/approve_key"/>
  <jfs:oauthRequestTokenUrl rdf:resource="{b}/oauth/request_token"/>
  <jfs:oauthUserAuthorizationUrl rdf:resource="{b}/oauth/authorize"/>
  <jfs:oauthAccessTokenUrl rdf:resource="{b}/oauth/access_token"/>
</rdf:Description>
""".format(b=b, rm=RM, am=AM)
        self.send(200, body, "application/rdf+xml; charset=utf-8")

    def catalog(self):
        b = self.base()
        self.rdf("""  <oslc:ServiceProviderCatalog rdf:about="{b}/oslc/catalog">
    <dcterms:title>DOOM OSLC Catalog</dcterms:title>
    <dcterms:description>Service providers that rip and tear</dcterms:description>
    <oslc:domain rdf:resource="{rm}"/>
    <oslc:domain rdf:resource="{am}"/>
    <oslc:serviceProvider>
{sp}
    </oslc:serviceProvider>
  </oslc:ServiceProviderCatalog>""".format(b=b, rm=RM, am=AM, sp=self.provider_xml(b)))

    def provider(self):
        self.rdf(self.provider_xml(self.base()))

    def provider_xml(self, b):
        def dialog(kind, title, label):
            return """          <oslc:{kind}Dialog>
            <oslc:Dialog>
              <dcterms:title>{title}</dcterms:title>
              <oslc:label>{label}</oslc:label>
              <oslc:dialog rdf:resource="{b}/oslc/dialog/{path}"/>
              <oslc:hintWidth>800px</oslc:hintWidth>
              <oslc:hintHeight>600px</oslc:hintHeight>
              <oslc:resourceType rdf:resource="{rm}Requirement"/>
              <oslc:resourceType rdf:resource="{am}Resource"/>
              <oslc:usage rdf:resource="http://open-services.net/ns/core#default"/>
            </oslc:Dialog>
          </oslc:{kind}Dialog>""".format(
                kind=kind, title=title, label=label, b=b, rm=RM, am=AM,
                path="select" if kind == "selection" else "create")

        return """      <oslc:ServiceProvider rdf:about="{b}/oslc/provider">
        <dcterms:title>DOOM</dcterms:title>
        <dcterms:description>Knee-deep in the requirements</dcterms:description>
        <dcterms:identifier>doom</dcterms:identifier>
        <oslc:details rdf:resource="{b}/"/>
        <oslc:service>
          <oslc:Service>
            <oslc:domain rdf:resource="{rm}"/>
            <oslc:domain rdf:resource="{am}"/>
{select}
{create}
            <oslc:creationFactory>
              <oslc:CreationFactory>
                <dcterms:title>New DOOM session</dcterms:title>
                <oslc:label>New DOOM session</oslc:label>
                <oslc:creation rdf:resource="{b}/oslc/runs"/>
                <oslc:resourceType rdf:resource="{rm}Requirement"/>
                <oslc:usage rdf:resource="http://open-services.net/ns/core#default"/>
              </oslc:CreationFactory>
            </oslc:creationFactory>
            <oslc:queryCapability>
              <oslc:QueryCapability>
                <dcterms:title>DOOM sessions</dcterms:title>
                <oslc:label>DOOM sessions</oslc:label>
                <oslc:queryBase rdf:resource="{b}/oslc/runs"/>
                <oslc:resourceType rdf:resource="{rm}Requirement"/>
                <oslc:usage rdf:resource="http://open-services.net/ns/core#default"/>
              </oslc:QueryCapability>
            </oslc:queryCapability>
          </oslc:Service>
        </oslc:service>
      </oslc:ServiceProvider>""".format(
            b=b, rm=RM, am=AM,
            select=dialog("selection", "Play DOOM", "Play DOOM"),
            create=dialog("creation", "New DOOM session", "New DOOM session"))

    # ---- OSLC resources -------------------------------------------------

    def run_xml(self, b, run_id):
        return """  <oslc_rm:Requirement rdf:about="{b}/oslc/runs/{id}">
    <rdf:type rdf:resource="{am}Resource"/>
    <dcterms:title>{title}</dcterms:title>
    <dcterms:identifier>{id}</dcterms:identifier>
    <dcterms:description>A game of DOOM, embedded through OSLC delegated UI.</dcterms:description>
    <oslc:shortTitle>{id}</oslc:shortTitle>
    <oslc:serviceProvider rdf:resource="{b}/oslc/provider"/>
  </oslc_rm:Requirement>""".format(b=b, id=run_id, am=AM, title=escape(run_title(run_id)))

    def run(self, run_id, query):
        b = self.base()
        accept = self.headers.get("Accept", "").lower()
        about = "%s/oslc/runs/%s" % (b, run_id)
        if "preview" in query:
            return self.play("view", query, run_id)
        if "x-oslc-compact" in accept:
            def preview(kind, w, h):
                return """    <oslc:{kind}Preview>
      <oslc:Preview>
        <oslc:document rdf:resource="{about}?preview={kind}"/>
        <oslc:hintWidth>{w}</oslc:hintWidth>
        <oslc:hintHeight>{h}</oslc:hintHeight>
      </oslc:Preview>
    </oslc:{kind}Preview>""".format(kind=kind, about=about, w=w, h=h)
            return self.rdf("""  <oslc:Compact rdf:about="{about}">
    <dcterms:title>{title}</dcterms:title>
    <oslc:shortTitle>{id}</oslc:shortTitle>
    <oslc:icon rdf:resource="{b}/icon.svg"/>
{small}
{large}
  </oslc:Compact>""".format(about=about, title=escape(run_title(run_id)), id=run_id, b=b,
                            small=preview("small", "480px", "390px"),
                            large=preview("large", "800px", "640px")),
                            ctype="application/x-oslc-compact+xml")
        if "text/html" in accept:
            return self.play("view", query, run_id)
        self.rdf(self.run_xml(b, run_id))

    def query_runs(self):
        b = self.base()
        members = "\n".join('    <rdfs:member rdf:resource="%s/oslc/runs/%s"/>' % (b, r) for r in SAMPLE_RUNS)
        resources = "\n".join(self.run_xml(b, r) for r in SAMPLE_RUNS)
        self.rdf("""  <oslc:ResponseInfo rdf:about="{b}/oslc/runs">
    <dcterms:title>DOOM sessions</dcterms:title>
    <oslc:totalCount>{n}</oslc:totalCount>
  </oslc:ResponseInfo>
  <rdf:Description rdf:about="{b}/oslc/runs">
{members}
  </rdf:Description>
{resources}""".format(b=b, n=len(SAMPLE_RUNS), members=members, resources=resources))

    def create_run(self):
        self.read_body()
        b = self.base()
        run_id = "run-" + secrets.token_hex(4)
        self.rdf(self.run_xml(b, run_id), 201, {"Location": "%s/oslc/runs/%s" % (b, run_id)})

    # ---- OAuth 1.0a, wide open -----------------------------------------

    def oauth(self, path, query):
        form = parse_qs(self.read_body().decode("utf-8", "replace")) if self.command == "POST" else {}

        def param(name):
            return (query.get(name) or form.get(name) or [""])[0]

        token = param("oauth_token") or secrets.token_hex(8)
        if path == "/oauth/request_token":
            return self.send(200, urlencode({
                "oauth_token": secrets.token_hex(8),
                "oauth_token_secret": secrets.token_hex(8),
                "oauth_callback_confirmed": "true",
            }), "application/x-www-form-urlencoded")
        if path == "/oauth/access_token":
            return self.send(200, urlencode({
                "oauth_token": secrets.token_hex(8),
                "oauth_token_secret": secrets.token_hex(8),
            }), "application/x-www-form-urlencoded")
        if path == "/oauth/authorize":
            callback = param("oauth_callback")
            if callback.lower().startswith(("http://", "https://")):
                sep = "&" if "?" in callback else "?"
                target = callback + sep + urlencode({"oauth_token": token, "oauth_verifier": "doom"})
                return self.send(302, "", headers={"Location": target})
            return self.send(200, "<!doctype html><title>Authorized</title><p>Authorized. "
                             "Verifier: <code>doom</code>. You can close this window.</p>",
                             "text/html; charset=utf-8")
        if path == "/oauth/request_key":
            return self.send(200, json.dumps({"key": "doom-" + secrets.token_hex(4)}), "application/json")
        if path == "/oauth/approve_key":
            return self.send(200, "<!doctype html><title>Approved</title><p>Consumer key "
                             "<code>%s</code> approved.</p>" % escape(quote(param("key"))),
                             "text/html; charset=utf-8")
        self.send(404, "not found")


def main():
    games = available_games()
    if not games:
        print("WARNING: no game files found in %s" % GAME_DIR, file=sys.stderr)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("DOOM OSLC provider on :%d (games: %s)" % (PORT, ", ".join(games) or "none"), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
