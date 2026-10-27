import unittest
from unittest import mock

from fastapi import HTTPException

from app import engine, main


class TestUrlSplitting(unittest.TestCase):
    def test_commas_inside_urls_survive(self):
        url = "https://booru.example/posts?tags=cat,dog;rating=s"
        self.assertEqual(main.parse_urls(url), [url])

    def test_commas_between_urls_still_split(self):
        self.assertEqual(
            main.parse_urls("https://a/1,https://b/2;http://c/3,"),
            ["https://a/1", "https://b/2", "http://c/3"],
        )

    def test_report_counts_invalid_and_returns_rest(self):
        raw = "nope " + " ".join(f"http://a/{i}" for i in range(main.MAX_BATCH + 5))
        urls, invalid, rest = main.parse_urls_report(raw)
        self.assertEqual(len(urls), main.MAX_BATCH)
        self.assertEqual(invalid, 1)
        self.assertEqual(rest, [f"http://a/{i}" for i in range(main.MAX_BATCH, main.MAX_BATCH + 5)])


class TestCleanRange(unittest.TestCase):
    def test_all_means_everything(self):
        for text in ("", "  ", "all", "ALL", "*", None):
            self.assertEqual(main.clean_range(text), "")

    def test_valid_ranges(self):
        self.assertEqual(main.clean_range("1-80"), "1-80")
        self.assertEqual(main.clean_range(" 1 - 10 , 25-30 "), "1-10,25-30")
        self.assertEqual(main.clean_range("5"), "5")
        self.assertEqual(main.clean_range("-2,10-"), "-2,10-")
        self.assertEqual(main.clean_range("4:8:2"), "4:8:2")

    def test_invalid_ranges(self):
        for text in ("abc", "1-80x", ",", "-", "1..5"):
            with self.assertRaises(HTTPException, msg=text) as ctx:
                main.clean_range(text)
            self.assertEqual(ctx.exception.status_code, 400)


class TestEnqueueShape(unittest.TestCase):
    def test_reports_queued_invalid_and_rest(self):
        made = []

        def fake_create(url, **kw):
            made.append((url, kw["range_spec"]))
            return {"id": str(len(made)), "url": url}

        with mock.patch.object(main, "_require_terms", return_value={"scan_limit": 100}), \
                mock.patch.object(main.jobs, "create_job", side_effect=fake_create):
            out = main.scan(main.UrlsIn(url="https://a/x?t=1,2 junk", range="all"))
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["invalid"], 1)
        self.assertEqual(out["rest"], [])
        self.assertEqual(made, [("https://a/x?t=1,2", "1-100")])

    def test_bad_range_rejected_before_queueing(self):
        with mock.patch.object(main, "_require_terms", return_value={}), \
                mock.patch.object(main.jobs, "create_job") as create:
            with self.assertRaises(HTTPException):
                main.grab(main.UrlsIn(url="https://a/x", range="all of it"))
        create.assert_not_called()


class TestRetries(unittest.TestCase):
    def test_zero_is_kept(self):
        self.assertEqual(engine._retries({"retries": 0}), 0)
        self.assertEqual(engine._retries({"retries": 7}), 7)
        self.assertEqual(engine._retries({}), 4)


class TestUpdateBlocking(unittest.TestCase):
    def _patches(self, active):
        return [
            mock.patch.object(engine, "ensure_local_gallery_dl", lambda: None),
            mock.patch.object(engine, "can_self_update", lambda: True),
            mock.patch.object(engine.time, "sleep", lambda s: None),
            mock.patch.object(engine.jobs, "active_count", lambda: active),
        ]

    def test_refuses_while_jobs_run(self):
        patches = self._patches(active=2)
        for p in patches:
            p.start()
        try:
            with mock.patch.object(engine, "_run_update") as run:
                with self.assertRaisesRegex(RuntimeError, "running downloads"):
                    engine.update_engine()
                run.assert_not_called()
            self.assertFalse(engine.is_updating())
        finally:
            for p in patches:
                p.stop()

    def test_holds_scheduler_during_update(self):
        patches = self._patches(active=0)
        for p in patches:
            p.start()
        seen = []
        try:
            with mock.patch.object(engine, "_run_update", side_effect=lambda: seen.append(engine.is_updating()) or {}):
                engine.update_engine()
            self.assertEqual(seen, [True])
            self.assertFalse(engine.is_updating())
        finally:
            for p in patches:
                p.stop()


if __name__ == "__main__":
    unittest.main()
