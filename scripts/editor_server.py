"""Private, loopback-only content editor for the static website (Python 3.11+)."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import tomllib
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
MAX_BODY = 2 * 1024 * 1024
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FILENAME = re.compile(r"(?:\d{4}-\d{2}-\d{2}-)?[a-z0-9]+(?:-[a-z0-9]+)*\.md\Z")


class EditorError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def revision(raw):
    return sha256(raw).hexdigest()


def require_string(value, label, *, nonempty=False, maximum=2000000):
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value.strip()):
        raise EditorError(f"{label} must be {'a nonempty' if nonempty else 'a'} string (maximum {maximum} characters).")
    # Lone UTF-16 surrogates cannot be saved as UTF-8.
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise EditorError(f"{label} contains invalid Unicode.") from None
    return value


def string_list(value, label):
    if not isinstance(value, list) or len(value) > 1000:
        raise EditorError(f"{label} must be a list of strings (up to 1000 entries).")
    for item in value:
        require_string(item, label, maximum=100000)
    return value


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise EditorError("Date must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise EditorError("Date must be a valid calendar date.") from None


def validate_profile(data):
    if not isinstance(data, dict):
        raise EditorError("Profile must be a JSON object.")
    for key in ("name", "display_name", "role", "institution", "location", "email", "orcid", "github",
                "scholar_url", "group_url", "intro"):
        require_string(data.get(key), key, nonempty=key in {"name", "display_name"}, maximum=100000)
    for key in ("scholar_url", "group_url"):
        if data[key]:
            parts = urlsplit(data[key])
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise EditorError(f"{key} must be an HTTP or HTTPS URL.")
    string_list(data.get("about"), "about")
    schemas = {
        "research": (("id", "title", "kicker", "summary", "institution", "period", "advisor", "status"),
                     ("details", "methods", "related_dois")),
        "education": (("institution", "degree", "period"), ()),
        "honors": (("title", "organization", "year"), ()),
        "skills": (("category",), ("items",)),
    }
    for key, (strings, arrays) in schemas.items():
        records = data.get(key)
        if not isinstance(records, list) or len(records) > 1000:
            raise EditorError(f"{key} must be a list (up to 1000 entries).")
        if key == "research" and not records:
            raise EditorError("Keep at least one research project; the homepage uses the first project's advisor.")
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise EditorError(f"{key}[{index}] must be an object.")
            for field in strings:
                require_string(record.get(field), f"{key}[{index}].{field}", maximum=100000)
            for field in arrays:
                string_list(record.get(field), f"{key}[{index}].{field}")
    identifiers = [record["id"] for record in data["research"]]
    if any(not SLUG.fullmatch(identifier) for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise EditorError("Research IDs must be unique lowercase words/numbers joined by hyphens.")
    return data


def validate_manual(data):
    if not isinstance(data, dict):
        raise EditorError("Manual publications must be a JSON object.")
    for key, required in (("manuscripts", ("title", "venue", "status", "note")), ("patents", ("title", "number"))):
        items = data.get(key)
        if not isinstance(items, list) or len(items) > 1000:
            raise EditorError(f"{key} must be a list (up to 1000 entries).")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise EditorError(f"{key}[{index}] must be an object.")
            for field in required:
                require_string(item.get(field), f"{key}[{index}].{field}", maximum=100000)
            if key == "manuscripts":
                string_list(item.get("authors"), f"{key}[{index}].authors")
            for optional in ("doi", "url", "status"):
                if optional in item:
                    require_string(item[optional], f"{key}[{index}].{optional}", maximum=100000)
    return data


def toml_value(value):
    """Encode parsed TOML values, including extra metadata unknown to the editor."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{toml_value(key)} = {toml_value(item)}" for key, item in value.items()) + " }"
    raise EditorError("Unsupported existing TOML metadata; edit the source file directly.")


class Application:
    def __init__(self, root=ROOT, build_function=None):
        self.root = Path(root).resolve()
        self.token = secrets.token_urlsafe(32)
        self.build_function = build_function
        self.lock = threading.RLock()

    def path(self, relative, *, must_exist=False):
        """Never cross symlinks/junctions or let a path leave the checkout."""
        parts = Path(relative).parts
        if not parts or Path(relative).is_absolute() or any(part in {"..", "."} or ":" in part or "\\" in part for part in parts):
            raise EditorError("Invalid path.")
        candidate = self.root
        for part in parts:
            candidate /= part
            if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                raise EditorError("Symlinks and directory junctions are not supported in editor paths.")
        if not candidate.resolve().is_relative_to(self.root):
            raise EditorError("Path is outside the website folder.")
        if must_exist and not candidate.is_file():
            raise EditorError("File not found.", 404)
        return candidate

    def post_path(self, filename, *, must_exist=True):
        if not isinstance(filename, str) or len(filename) > 200 or not FILENAME.fullmatch(filename):
            raise EditorError("Invalid note filename.")
        return self.path(Path("content/posts") / filename, must_exist=must_exist)

    def read_post(self, filename, *, include_body=True):
        raw = self.post_path(filename).read_bytes()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        if not text.startswith("+++\n") or "\n+++\n" not in text[4:]:
            raise EditorError(f"{filename}: expected TOML metadata between +++ lines.")
        header, body = text[4:].split("\n+++\n", 1)
        try:
            metadata = tomllib.loads(header)
        except tomllib.TOMLDecodeError as error:
            raise EditorError(f"{filename}: {error}") from None
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", filename[:-3])
        post = {"filename": filename, "title": metadata.get("title", ""), "date": str(metadata.get("date", "")),
                "description": metadata.get("description", ""), "tags": metadata.get("tags", []),
                "draft": metadata.get("draft", True), "slug": slug, "revision": revision(raw)}
        if include_body:
            post["body"] = body
        return post, metadata

    def list_posts(self):
        folder = self.path("content/posts")
        if not folder.exists():
            return []
        posts = [self.read_post(path.name, include_body=False)[0] for path in folder.glob("*.md")]
        return sorted(posts, key=lambda item: (item["date"], item["filename"]), reverse=True)

    def check_revision(self, path, expected, *, new=False):
        if new:
            if path.exists():
                raise EditorError("A note with this filename already exists. Choose a different URL slug.", 409)
            return
        if not path.is_file():
            raise EditorError("This file was removed. Reload the editor before saving.", 409)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or not secrets.compare_digest(revision(path.read_bytes()), expected):
            raise EditorError("This file changed since you opened it. Reload it before saving to avoid overwriting changes.", 409)

    def write(self, path, raw):
        # Preserve the exact previous bytes, including BOM and line endings.
        if path.exists():
            old = path.read_bytes()
            if old == raw:
                return
            relative = path.relative_to(self.root)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup = self.path(Path(".local/editor-backups") / relative.parent / f"{timestamp}-{secrets.token_hex(4)}-{path.name}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as handle:
                handle.write(old)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".editor-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def rebuild(self):
        try:
            if self.build_function is not None:
                self.build_function()
            else:
                import build_site
                build_site.build()
            return {"build_ok": True, "build_error": None}
        except Exception as error:
            return {"build_ok": False, "build_error": f"{type(error).__name__}: {error}"}

    def save_post(self, payload):
        for field in ("title", "date", "description", "slug", "body"):
            require_string(payload.get(field), field, nonempty=field in {"title", "date", "slug"})
        published = iso_date(payload["date"])
        string_list(payload.get("tags"), "tags")
        if not isinstance(payload.get("draft"), bool):
            raise EditorError("Draft must be true or false.")
        slug = payload["slug"]
        if len(slug) > 140 or not SLUG.fullmatch(slug):
            raise EditorError("URL slug must be lowercase letters/numbers joined by hyphens (up to 140 characters).")
        filename = payload.get("filename")
        new = filename is None
        metadata = {}
        if new:
            filename = f"{payload['date']}-{slug}.md"
        path = self.post_path(filename, must_exist=False)
        self.check_revision(path, payload.get("revision"), new=new)
        if not new:
            current, metadata = self.read_post(filename)
            if slug != current["slug"]:
                raise EditorError("The URL slug of an existing note is fixed to preserve its links.")
        if any(post["slug"] == slug and post["filename"] != filename for post in self.list_posts()):
            raise EditorError("Another note already uses this URL slug, including drafts. Choose a different slug.", 409)
        metadata.update({key: payload[key] for key in ("title", "date", "description", "tags", "draft")})
        header = "\n".join(f"{toml_value(key)} = {toml_value(value)}" for key, value in metadata.items())
        tomllib.loads(header)  # Verify escaping and unknown metadata before replacing the source.
        raw = (f"+++\n{header}\n+++\n" + payload["body"].replace("\r\n", "\n")).encode("utf-8")
        if len(raw) > MAX_BODY:
            raise EditorError("Note exceeds the 2 MiB limit.")
        self.write(path, raw)
        post, _ = self.read_post(filename)
        result = {"post": post, **self.rebuild()}
        result["preview_url"] = f"/blog/{slug}/" if not payload["draft"] and published <= datetime.now(timezone.utc).date() and result["build_ok"] else None
        return result

    def data_path(self, name):
        if name not in {"profile", "manual-publications"}:
            raise EditorError("Only profile and manual-publications can be edited here.")
        return self.path(f"data/{name}.json", must_exist=True)

    def read_data(self, name):
        raw = self.data_path(name).read_bytes()
        return {"data": json.loads(raw.decode("utf-8-sig")), "revision": revision(raw)}

    def save_data(self, payload):
        name = payload.get("name")
        path = self.data_path(name)
        self.check_revision(path, payload.get("revision"))
        data = payload.get("data")
        (validate_profile if name == "profile" else validate_manual)(data)
        raw = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_BODY:
            raise EditorError("Data exceeds the 2 MiB limit.")
        self.write(path, raw)
        return {"data": data, "revision": revision(raw), **self.rebuild()}

    def render(self, payload):
        import markdown
        title = require_string(payload.get("title", ""), "title")
        body = require_string(payload.get("body", ""), "body")
        published = require_string(payload.get("date", ""), "date")
        description = require_string(payload.get("description", ""), "description")
        html = markdown.markdown(body, extensions=["fenced_code", "tables", "toc", "sane_lists"])
        # This is also sandboxed by the editor iframe, without allow-scripts or allow-same-origin.
        policy = "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: http:; font-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
        document = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    f'<meta http-equiv="Content-Security-Policy" content="{escape(policy, quote=True)}">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<link rel="stylesheet" href="/assets/site.css">'
                    '<style>body{background:#fff;color:#1c312c;margin:0;font:17px/1.75 Georgia,serif}.post{padding:32px;max-width:850px;margin:auto}h1{font-size:2.6rem;line-height:1.15}.prose{overflow-wrap:anywhere}.prose img{max-width:100%}pre{overflow:auto;padding:16px;background:#f3f5f3}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}blockquote{border-left:3px solid #b4c7bd;padding-left:16px}.eyebrow{font:12px sans-serif;color:#53675f}.lede{color:#53675f}</style>'
                    f'<title>{escape(title)}</title></head><body><article class="post"><p class="eyebrow">{escape(published)}</p>'
                    f'<h1>{escape(title)}</h1><p class="lede">{escape(description)}</p><div class="prose">{html}</div></article></body></html>')
        return {"html": document}


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "LocalContentEditor/1.0"

    @property
    def app(self):
        return self.server.application

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_bytes(self, raw, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https: http:; connect-src 'self' https://pub.orcid.org; font-src 'self'; object-src 'none'; frame-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def guard(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"} or len(self.headers.get_all("Host", [])) != 1:
            raise EditorError("Use the local editor address printed in the terminal.", 403)
        if self.command not in {"GET", "HEAD"}:
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                raise EditorError("Requests from another website are not allowed.", 403)

    def api_guard(self):
        token = self.headers.get("X-Editor-Token", "")
        if not secrets.compare_digest(token.encode("utf-8"), self.app.token.encode("ascii")):
            raise EditorError("Editor session expired. Reload the local editor page.", 403)

    def payload(self):
        if self.headers.get("Transfer-Encoding"):
            raise EditorError("Chunked requests are not supported.")
        if self.headers.get_content_type() != "application/json":
            raise EditorError("Requests must use application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            raise EditorError("Invalid request size.") from None
        if length < 0 or length > MAX_BODY:
            raise EditorError("Request exceeds the 2 MiB limit or has no size.", 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise EditorError("Incomplete request.")
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError("Invalid number")))
        except (ValueError, UnicodeError):
            raise EditorError("Request must contain valid JSON.") from None
        if not isinstance(value, dict):
            raise EditorError("Request must contain a JSON object.")
        return value

    def dispatch(self):
        self.guard()
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            raise EditorError("Absolute request paths are not supported.")
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            self.api_guard()
            query = parse_qs(parsed.query, keep_blank_values=True)
            if any(len(values) != 1 for values in query.values()):
                raise EditorError("Duplicate query parameters are not supported.")
            if self.command in {"GET", "HEAD"}:
                if path == "/api/posts":
                    result = {"post": self.app.read_post(query["filename"][0])[0]} if "filename" in query else {"posts": self.app.list_posts()}
                elif path == "/api/data":
                    result = self.app.read_data(query.get("name", [None])[0])
                else:
                    raise EditorError("API endpoint not found.", 404)
            elif self.command == "POST":
                value = self.payload()
                routes = {"/api/posts": self.app.save_post, "/api/data": self.app.save_data, "/api/render": self.app.render,
                          "/api/build": lambda payload: self.app.rebuild()}
                if path not in routes:
                    raise EditorError("API endpoint not found.", 404)
                result = routes[path](value)
            else:
                raise EditorError("Method not allowed.", 405)
            self.send_json(result)
            return
        if self.command not in {"GET", "HEAD"}:
            raise EditorError("Method not allowed.", 405)
        if path == "/editor":
            self.send_response(302)
            self.send_header("Location", "/editor/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        is_editor = path.startswith("/editor/")
        relative = path[len("/editor/"):] if is_editor else path.lstrip("/")
        if any(part in {"..", "."} or part.startswith(".") or ":" in part or "\\" in part for part in relative.split("/")) or "\x00" in relative:
            raise EditorError("Invalid file path.", 404)
        folder = "editor" if is_editor else "_site"
        candidate = self.app.path(Path(folder) / relative)
        if candidate.is_dir():
            candidate = self.app.path(Path(folder) / relative / "index.html", must_exist=True)
        elif not candidate.is_file():
            raise EditorError("Page not found. Use /editor/ to edit or rebuild the website.", 404)
        raw = candidate.read_bytes()
        if is_editor and candidate == self.app.root / "editor/index.html":
            raw = raw.replace(b"__EDITOR_TOKEN__", self.app.token.encode("ascii"))
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if content_type.startswith("text/") or candidate.suffix in {".js", ".json"}:
            content_type += "; charset=utf-8"
        self.send_bytes(raw, content_type)

    def handle_request(self):
        try:
            # Browser preconnections can idle independently, but source changes,
            # builds and static snapshots are serialized to avoid races.
            with self.app.lock:
                self.dispatch()
        except EditorError as error:
            self.send_json({"error": str(error)}, error.status)
        except (OSError, ValueError, TypeError) as error:
            self.send_json({"error": f"Unable to process this content: {error}"}, 400)
        except Exception:
            self.send_json({"error": "The local editor encountered an unexpected error. Check the terminal and reload."}, 500)
            import traceback
            traceback.print_exc()

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request
    do_OPTIONS = handle_request


def make_server(port=8000, *, application=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), EditorHandler)
    server.application = application or Application()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-build", action="store_true", help="Skip the startup site build.")
    parser.add_argument("--open-browser", action="store_true", help="Open the local editor in your default browser.")
    args = parser.parse_args()
    app = Application()
    server = make_server(args.port, application=app)
    if not args.no_build:
        result = app.rebuild()
        if not result["build_ok"]:
            print(f"Initial build needs attention: {result['build_error']}", flush=True)
    url = f"http://127.0.0.1:{server.server_address[1]}/editor/"
    print(f"Local editor: {url}\nChanges save to this folder. Use update-site.ps1 to publish.\nPress Ctrl+C to stop.", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEditor stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
