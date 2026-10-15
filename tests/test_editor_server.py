"""Persistence and HTTP boundary checks for the local content editor."""
from datetime import date
import http.client
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("editor_server", ROOT / "scripts/editor_server.py")
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)


class EditorFixture(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.folder = Path(self.temp.name)
        for folder in ("content/posts", "data", "editor", "_site"):
            (self.folder / folder).mkdir(parents=True)
        (self.folder / "editor/index.html").write_text('<meta name="editor-token" content="__EDITOR_TOKEN__">', encoding="utf-8")
        (self.folder / "_site/index.html").write_text("Published website", encoding="utf-8")
        (self.folder / "private.pdf").write_bytes(b"Private source")
        for name in ("profile.json", "manual-publications.json", "publications.json"):
            shutil.copyfile(ROOT / "data" / name, self.folder / "data" / name)
        self.builds = 0
        self.app = editor.Application(self.folder, self.build)

    def build(self):
        self.builds += 1

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, **values):
        return {"filename": None, "revision": None, "title": 'A note: "中文" and \\ paths', "date": "2020-01-02",
                "description": "An abstract\nwith a newline", "tags": ["Research", 'Quoted "text"'], "draft": True,
                "slug": "test-note", "body": "\n## Heading\n\n**Research** content.\n", **values}


class PersistenceTests(EditorFixture):
    def test_new_note_toml_unicode_roundtrip_and_safe_draft_default(self):
        payload = self.payload()
        saved = self.app.save_post(payload)
        self.assertTrue(saved["build_ok"])
        self.assertIsNone(saved["preview_url"])
        self.assertEqual(self.builds, 1)
        post = saved["post"]
        self.assertEqual(post["filename"], "2020-01-02-test-note.md")
        for key in ("title", "date", "description", "tags", "draft", "slug", "body"):
            self.assertEqual(post[key], payload[key])
        self.assertNotIn("body", self.app.list_posts()[0])
        self.assertEqual(len(post["revision"]), 64)

    def test_update_keeps_filename_and_slug_and_backs_up_exact_bytes(self):
        post = self.app.save_post(self.payload())["post"]
        path = self.folder / "content/posts" / post["filename"]
        original = path.read_bytes()
        updated = self.app.save_post({**post, "date": "2020-03-04", "body": "Changed"})["post"]
        self.assertEqual(updated["filename"], post["filename"])
        backups = list((self.folder / ".local/editor-backups/content/posts").glob("*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertNotEqual(updated["revision"], post["revision"])
        with self.assertRaisesRegex(editor.EditorError, "fixed"):
            self.app.save_post({**updated, "slug": "changed-slug"})

    def test_stale_revision_never_overwrites_external_changes(self):
        post = self.app.save_post(self.payload())["post"]
        path = self.folder / "content/posts" / post["filename"]
        path.write_bytes(path.read_bytes() + b"\nExternal edit")
        external = path.read_bytes()
        with self.assertRaises(editor.EditorError) as caught:
            self.app.save_post({**post, "body": "stale overwrite"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(path.read_bytes(), external)
        self.assertFalse((self.folder / ".local").exists())

    def test_duplicate_slug_across_drafts_and_dates_is_rejected(self):
        self.app.save_post(self.payload())
        for changed in ({}, {"date": "2021-01-02"}, {"date": "2021-01-02", "draft": False}):
            with self.assertRaises(editor.EditorError) as caught:
                self.app.save_post(self.payload(**changed))
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(list((self.folder / "content/posts").glob("*.md"))), 1)

    def test_unknown_toml_metadata_survives_edit(self):
        filename = "2020-01-02-extra.md"
        path = self.folder / "content/posts" / filename
        path.write_text('+++\ntitle="Extra"\ndate=2020-01-02\ndraft=true\nweight=2\n'
                        'options={ enabled=true, aliases=["one", "two"], at=2020-01-02T12:00:00Z }\n+++\nBody', encoding="utf-8")
        post, original_meta = self.app.read_post(filename)
        self.app.save_post({**post, "body": "Updated body"})
        updated, meta = self.app.read_post(filename)
        self.assertEqual(meta["options"], original_meta["options"])
        self.assertEqual(meta["weight"], 2)
        self.assertEqual(updated["body"], "Updated body")

    def test_invalid_note_never_creates_a_file(self):
        for bad in ({"slug": "../../outside"}, {"filename": "../profile.json"}, {"date": "2020-02-31"},
                    {"title": ""}, {"tags": "tag"}, {"draft": "false"}, {"body": "\ud800"}):
            with self.subTest(bad=bad), self.assertRaises(editor.EditorError):
                self.app.save_post(self.payload(**bad))
        self.assertEqual(self.app.list_posts(), [])

    def test_json_validation_revision_and_unknown_keys(self):
        original = self.app.read_data("profile")
        original["data"]["intro"] = "Updated introduction"
        original["data"]["future_field"] = {"nested": ["kept"]}
        saved = self.app.save_data({"name": "profile", **original})
        self.assertEqual(saved["data"]["future_field"], {"nested": ["kept"]})
        with self.assertRaises(editor.EditorError) as caught:
            self.app.save_data({"name": "profile", **original})
        self.assertEqual(caught.exception.status, 409)
        saved["data"]["research"] = []
        with self.assertRaisesRegex(editor.EditorError, "at least one"):
            self.app.save_data({"name": "profile", "data": saved["data"], "revision": saved["revision"]})
        self.assertTrue(self.app.read_data("profile")["data"]["research"])
        with self.assertRaises(editor.EditorError):
            self.app.read_data("publications")
        with self.assertRaises(editor.EditorError):
            self.app.read_data("../private")

    def test_manual_publications_validate_required_shape(self):
        original = self.app.read_data("manual-publications")
        original["data"]["manuscripts"][0]["authors"] = "Not a list"
        with self.assertRaises(editor.EditorError):
            self.app.save_data({"name": "manual-publications", **original})
        original["data"] = {"manuscripts": [], "patents": []}
        saved = self.app.save_data({"name": "manual-publications", **original})
        self.assertEqual(saved["data"], {"manuscripts": [], "patents": []})

    def test_build_failure_reports_saved_source(self):
        def failing_build():
            raise ValueError("An unrelated post is invalid")
        self.app.build_function = failing_build
        result = self.app.save_post(self.payload(draft=False))
        self.assertFalse(result["build_ok"])
        self.assertIn("An unrelated post", result["build_error"])
        self.assertIsNone(result["preview_url"])
        self.assertTrue(self.app.post_path(result["post"]["filename"]).is_file())

    def test_render_isolated_preview_supports_markdown_and_raw_html(self):
        result = self.app.render({"title": "<Title>", "date": "2020-01-02", "body": "**Bold**\n\n<em>HTML</em><script>alert(1)</script>"})
        self.assertIn("<strong>Bold</strong>", result["html"])
        self.assertIn("<em>HTML</em>", result["html"])
        self.assertIn("&lt;Title&gt;", result["html"])
        self.assertIn("script-src &#x27;none&#x27;", result["html"])
        self.assertLess(result["html"].index("Content-Security-Policy"), result["html"].index("<script>"))

    def test_real_build_excludes_drafts_and_future_posts(self):
        site_spec = importlib.util.spec_from_file_location("editor_test_site", ROOT / "scripts/build_site.py")
        site = importlib.util.module_from_spec(site_spec)
        site_spec.loader.exec_module(site)
        shutil.copytree(ROOT / "templates", self.folder / "templates")
        shutil.copytree(ROOT / "assets", self.folder / "assets")
        (self.folder / "_site/.generated-site").write_text("Generated test", encoding="utf-8")
        with patch.object(site, "ROOT", self.folder):
            self.app.build_function = site.build
            draft = self.app.save_post(self.payload())
            self.assertTrue(draft["build_ok"], draft["build_error"])
            self.assertFalse((self.folder / "_site/blog/test-note").exists())
            published = self.app.save_post({**draft["post"], "draft": False})
            self.assertTrue(published["build_ok"], published["build_error"])
            self.assertEqual(published["preview_url"], "/blog/test-note/")
            self.assertTrue((self.folder / "_site/blog/test-note/index.html").is_file())
            future = self.app.save_post({**published["post"], "date": "2999-01-01"})
            self.assertTrue(future["build_ok"], future["build_error"])
            self.assertIsNone(future["preview_url"])
            self.assertFalse((self.folder / "_site/blog/test-note").exists())
        self.assertFalse((self.folder / "_site/editor").exists())
        self.assertFalse((self.folder / "_site/private.pdf").exists())


class HTTPTests(EditorFixture):
    def setUp(self):
        super().setUp()
        self.server = editor.make_server(0, application=self.app)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def request(self, method, path, payload=None, headers=None, *, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {"Content-Type": "application/json"}
        if authenticated:
            request_headers["X-Editor-Token"] = self.app.token
        request_headers.update(headers or {})
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def test_editor_injects_token_public_site_and_private_sources_stay_separate(self):
        status, headers, body = self.request("GET", "/editor/", authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertNotIn(b"__EDITOR_TOKEN__", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("GET", "/")[2], b"Published website")
        for path in ("/private.pdf", "/data/profile.json", "/.git/config", "/.local/editor-backups", "/content/posts/"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_api_auth_host_and_exact_origin(self):
        self.assertEqual(self.request("GET", "/api/posts", authenticated=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/posts", self.payload(), authenticated=False)[0], 403)
        self.assertEqual(self.request("GET", "/editor/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/posts", headers={"Host": "127.0.0.1:1"})[0], 403)
        for origin in ("https://evil.example", "null", f"http://localhost:{self.port}", "http://127.0.0.1:1"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request("POST", "/api/posts", self.payload(), {"Origin": origin})[0], 403)
        self.assertEqual(self.app.list_posts(), [])
        status, headers, body = self.request("POST", "/api/posts", self.payload(), {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200, body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        # Authenticated local command-line callers without Origin are supported.
        self.assertEqual(self.request("POST", "/api/build", {})[0], 200)

    def test_api_conflict_status_and_request_limits(self):
        status, _, body = self.request("POST", "/api/posts", self.payload())
        self.assertEqual(status, 200, body)
        post = json.loads(body)["post"]
        self.assertEqual(self.request("POST", "/api/posts", {**post, "revision": "stale"})[0], 409)
        self.assertEqual(self.request("POST", "/api/build", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/build", {}, {"Content-Length": str(editor.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request("GET", "/api/posts?filename=one&filename=two")[0], 400)
        self.assertEqual(self.request("POST", "/api/unknown", {})[0], 404)
        self.assertEqual(self.request("DELETE", "/api/posts")[0], 405)

    def test_encoded_traversal_and_absolute_source_paths_rejected(self):
        for path in ("/editor/%2e%2e/private.pdf", "/%2e%2e/private.pdf", "/editor/..%5cprivate.pdf",
                     "/editor/C:%5cprivate.pdf", "/editor/%00", "/api/posts?filename=..%2fprivate.pdf"):
            with self.subTest(path=path):
                self.assertIn(self.request("GET", path)[0], {400, 404})
        self.assertEqual(self.request("GET", "/api/data?name=publications")[0], 400)

    def test_symlink_sources_and_static_files_are_rejected(self):
        source = self.folder / "private.pdf"
        static_link = self.folder / "_site/leak.pdf"
        post_link = self.folder / "content/posts/2020-01-02-link.md"
        try:
            static_link.symlink_to(source)
            post_link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host")
        self.assertIn(self.request("GET", "/leak.pdf")[0], {400, 404})
        self.assertIn(self.request("GET", "/api/posts?filename=2020-01-02-link.md")[0], {400, 404})


if __name__ == "__main__":
    unittest.main()
