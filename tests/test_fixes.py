import asyncio
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gallery_dl import util as gdl_util
from PIL import Image

from app import engine, jobs, main, paths


class TestFilterConfig(unittest.TestCase):
    def _config(self, cfg, *, scan):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            with mock.patch.object(engine.settings, "output_dir", return_value=Path(tmp)), \
                    mock.patch.object(engine.settings, "archive_path", return_value=None):
                engine.write_config(path, cfg, scan=scan, range_spec="1-50")
            return json.loads(path.read_text(encoding="utf-8"))["extractor"]

    def test_filter_uses_gallery_dl_key_for_scan_and_download(self):
        cfg = {"filter_mode": "images", "min_width": 0}
        for scan in (True, False):
            extractor = self._config(cfg, scan=scan)
            self.assertNotIn("filter", extractor)
            self.assertIn("extension in", extractor["image-filter"])

    def test_no_filter_when_all(self):
        self.assertNotIn("image-filter", self._config({"filter_mode": "all"}, scan=False))

    def test_min_width_keeps_files_without_width(self):
        expr = engine._filter_expression({"filter_mode": "images", "min_width": 500})
        check = gdl_util.compile_filter(expr)
        self.assertTrue(check({"extension": "jpg"}))
        self.assertTrue(check({"extension": "jpg", "width": None}))
        self.assertTrue(check({"extension": "jpg", "width": 800}))
        self.assertFalse(check({"extension": "jpg", "width": 300}))
        self.assertFalse(check({"extension": "mp4", "width": 800}))


class TestPositions(unittest.TestCase):
    def test_child_message_is_marked(self):
        item = engine.item_from_message([6, "http://x/album/2", {"title": "two"}], 5)
        self.assertTrue(item["child"])
        self.assertEqual(item["kind"], "album")
        file_item = engine.item_from_message([3, "http://x/a.jpg", {"extension": "jpg"}], 6)
        self.assertNotIn("child", file_item)

    def test_children_do_not_use_file_positions(self):
        items = [
            {"url": "http://x/1.jpg"},
            {"url": "http://x/album", "child": True},
            {"url": "http://x/2.jpg"},
        ]
        nxt = engine.number_items(items, 1, 241)
        self.assertEqual([it["index"] for it in items], [1, 2, 3])
        self.assertEqual([it["pos"] for it in items], [241, None, 242])
        self.assertEqual(nxt, 243)

    def test_item_pos_legacy_and_child(self):
        self.assertEqual(engine.item_pos({"index": 7}), 7)
        self.assertEqual(engine.item_pos({"index": 7, "pos": 3}), 3)
        self.assertIsNone(engine.item_pos({"index": 7, "child": True}))

    def test_selection_maps_to_positions_and_child_urls(self):
        items = [
            {"index": 1, "pos": 1},
            {"index": 2, "pos": None, "child": True, "url": "http://x/album/a"},
            {"index": 3, "pos": 2},
            {"index": 4, "pos": 3},
            {"index": 5, "pos": 4},
        ]
        range_spec, children = main.selection_jobs(items, [2, 3, 4, 99])
        self.assertEqual(range_spec, "2-3")
        self.assertEqual(children, ["http://x/album/a"])

    def test_selection_rejects_bad_child_url(self):
        items = [{"index": 1, "child": True, "url": "file:///C:/x"}]
        self.assertEqual(main.selection_jobs(items, [1]), ("", []))


class TestDownloadStatus(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(engine.download_status(0, 0), "done")
        self.assertEqual(engine.download_status(None, 3), "done")
        self.assertEqual(engine.download_status(4, 10), "partial")
        self.assertEqual(engine.download_status(8, 499), "partial")
        self.assertEqual(engine.download_status(12, 0, 5), "partial")
        self.assertIsNone(engine.download_status(8, 0))
        self.assertIsNone(engine.download_status(1, 0, 0))


class JobsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(jobs, "HISTORY_PATH", tmp / "history.json"),
            mock.patch.object(jobs, "SCANS_PATH", tmp / "scans.json"),
            mock.patch.object(jobs, "ensure_dirs", lambda *a, **k: None),
            mock.patch.dict(jobs._jobs, clear=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class TestScanRetry(JobsTestCase):
    def _finished_scan(self):
        job = jobs.create_job("http://x/album", kind="scan", range_spec="1-2")
        jobs.update_job(job["id"], add_item={"index": 1, "pos": 1})
        jobs.update_job(job["id"], add_item={"index": 2, "pos": 2})
        jobs.update_job(job["id"], status="done")
        return job["id"]

    def test_retry_clears_scan_items(self):
        jid = self._finished_scan()
        jobs.requeue_job(jid)
        self.assertEqual(jobs.get_items(jid), [])

    def test_extend_keeps_items_and_sets_range(self):
        jid = self._finished_scan()
        extended = jobs.extend_scan(jid, "3-4")
        self.assertEqual(extended["status"], "queued")
        self.assertEqual(extended["range"], "3-4")
        self.assertEqual(len(jobs.get_items(jid)), 2)

    def test_extend_refuses_running_scan(self):
        job = jobs.create_job("http://x/album", kind="scan")
        self.assertIsNone(jobs.extend_scan(job["id"], "3-4"))

    def test_active_scan_is_not_queued_twice(self):
        first = jobs.create_job("http://x/album", kind="scan", range_spec="1-10")
        again = jobs.create_job("http://x/album", kind="scan", range_spec="1-10")
        self.assertTrue(again.get("already"))
        self.assertEqual(again["id"], first["id"])
        other = jobs.create_job("http://x/album", kind="scan", range_spec="11-20")
        self.assertFalse(other.get("already"))
        self.assertNotEqual(other["id"], first["id"])
        jobs.update_job(first["id"], status="done")
        fresh = jobs.create_job("http://x/album", kind="scan", range_spec="1-10")
        self.assertNotEqual(fresh["id"], first["id"])

    def test_peek_has_no_heavy_lists(self):
        jid = self._finished_scan()
        peeked = jobs.peek(jid)
        self.assertNotIn("items", peeked)
        self.assertEqual(peeked["status"], "done")


class TestFileSize(JobsTestCase):
    def test_incremental_stats(self):
        root = Path(self.tmp.name)
        files = []
        for i in range(5):
            p = root / f"{i}.bin"
            p.write_bytes(b"x" * (i + 1))
            files.append(str(p))
        real_stat = os.stat
        calls = []

        def counting_stat(path, *a, **k):
            calls.append(path)
            return real_stat(path, *a, **k)

        with mock.patch.object(jobs.os, "stat", counting_stat):
            self.assertEqual(jobs._files_size("j", files[:3], "downloading"), 1 + 2 + 3)
            self.assertEqual(len(calls), 3)
            self.assertEqual(jobs._files_size("j", files, "downloading"), 15)
            self.assertEqual(len(calls), 5)  # only the two new files
            self.assertEqual(jobs._files_size("j", files, "downloading"), 15)
            self.assertEqual(len(calls), 5)  # cached
            self.assertEqual(jobs._files_size("j", files, "done"), 15)
            self.assertEqual(len(calls), 10)  # one full recount when finished
            self.assertEqual(jobs._files_size("j", files[:1], "downloading"), 1)


class TestThumbs(unittest.TestCase):
    def _jpeg(self, w, h):
        out = io.BytesIO()
        Image.new("RGB", (w, h), (40, 120, 200)).save(out, "JPEG", quality=95)
        return out.getvalue()

    def test_shrinks_large_images(self):
        data, ctype = main.shrink_thumb(self._jpeg(2400, 1600), "image/jpeg")
        self.assertEqual(ctype, "image/webp")
        with Image.open(io.BytesIO(data)) as im:
            self.assertLessEqual(max(im.size), main.THUMB_EDGE)

    def test_keeps_small_and_non_images(self):
        small = self._jpeg(100, 80)
        self.assertEqual(main.shrink_thumb(small, "image/jpeg"), (small, "image/jpeg"))
        self.assertEqual(main.shrink_thumb(b"not an image", "video/mp4"), (b"not an image", "video/mp4"))

    def test_cache_is_capped_by_bytes(self):
        with mock.patch.object(main, "_THUMB_CACHE", main.OrderedDict()), \
                mock.patch.object(main, "_thumb_cache_bytes", 0), \
                mock.patch.object(main, "THUMB_CACHE_BYTES", 250):
            for i in range(5):
                main._thumb_cache_put(f"u{i}", (b"x" * 100, "image/webp"))
            self.assertEqual(list(main._THUMB_CACHE), ["u3", "u4"])
            self.assertLessEqual(main._thumb_cache_bytes, 250)
            main._thumb_cache_put("huge", (b"x" * (main.THUMB_ENTRY_MAX + 1), "image/jpeg"))
            self.assertNotIn("huge", main._THUMB_CACHE)


def _call(method, path, host, origin=None):
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 5000),
        "server": ("127.0.0.1", 8775), "root_path": "",
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(main.app(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


class TestHostGuard(unittest.TestCase):
    def test_local_hosts_allowed(self):
        self.assertEqual(_call("GET", "/api/ready", "127.0.0.1:8775"), 200)
        self.assertEqual(_call("GET", "/api/ready", "localhost:8775"), 200)

    def test_rebinding_host_blocked(self):
        self.assertEqual(_call("GET", "/api/ready", "evil.example:8775"), 403)
        self.assertEqual(
            _call("POST", "/api/history/clear", "evil.example:8775", origin="http://evil.example:8775"), 403
        )

    def test_cross_origin_post_blocked(self):
        self.assertEqual(_call("POST", "/api/history/clear", "127.0.0.1:8775", origin="http://evil.example"), 403)


class TestPruneUnpacks(unittest.TestCase):
    def _unpack(self, root, name, *, stills=True, age_h=48):
        d = root / name
        (d / "web").mkdir(parents=True)
        if stills:
            (d / "web" / "stills.ico").write_bytes(b"ico")
            (d / "web" / "app.js").write_text("//")
        old = time.time() - age_h * 3600
        os.utime(d, (old, old))
        return d

    def test_only_old_idle_stills_unpacks_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = self._unpack(root, "_MEI111")
            other_app = self._unpack(root, "_MEI222", stills=False)
            fresh = self._unpack(root, "_MEI333", age_h=1)
            current = self._unpack(root, "_MEI444")
            removed = paths.prune_stale_unpacks(root, str(current.resolve()), time.time() - 24 * 3600)
            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertFalse((root / "_MEI111-stale").exists())
            for d in (other_app, fresh, current):
                self.assertTrue(d.exists(), d.name)

    @unittest.skipUnless(os.name == "nt", "rename-in-use semantics are Windows-only")
    def test_unpack_in_use_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            busy = self._unpack(root, "_MEI555")
            with open(busy / "web" / "app.js", "rb"):
                removed = paths.prune_stale_unpacks(root, None, time.time() - 24 * 3600)
            self.assertEqual(removed, 0)
            self.assertTrue((busy / "web" / "stills.ico").exists())


if __name__ == "__main__":
    unittest.main()
