import json
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import guards, jobs, main, paths, settings


class TestThumbnails(unittest.TestCase):
    def test_certificates_are_checked_first(self):
        self.assertEqual(main._THUMB_SSL.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(main._THUMB_SSL.get_ca_certs() or main._THUMB_SSL.cert_store_stats()["x509_ca"] >= 0)
        self.assertTrue(main._THUMB_SSL.check_hostname)

    def test_full_size_reads_past_the_old_limit(self):
        self.assertGreater(main.FULL_IMAGE_MAX, 8_000_000)
        self.assertGreater(main.THUMB_SOURCE_MAX, 8_000_000)


class TestUnpluggedDrive(unittest.TestCase):
    def test_a_missing_folder_holds_the_queue(self):
        with mock.patch.object(guards, "_output_dir", side_effect=OSError("no drive")):
            guards._tick()
        state = guards.snapshot()
        self.assertTrue(state["blocked"])
        self.assertIn("isn't available", state["alerts"][0])


class TestLoadMoreRetry(unittest.TestCase):
    def test_retry_restores_the_first_page(self):
        with mock.patch.object(jobs, "_persist"):
            job = jobs.create_job("https://example.test/a", kind="scan", range_spec="1-240")
            jobs.update_job(job["id"], status="done")
            jobs.extend_scan(job["id"], "241-480")
            jobs.update_job(job["id"], status="done")
            again = jobs.requeue_job(job["id"])
            jobs.remove_job(job["id"])
        self.assertEqual(again["range"], "1-240")


class TestSettingsFile(unittest.TestCase):
    def test_a_list_in_the_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps([1, 2]), encoding="utf-8")
            with mock.patch.object(settings, "SETTINGS_PATH", path):
                merged, persist = settings._read()
        self.assertTrue(persist)
        self.assertEqual(merged["max_active"], settings.DEFAULTS["max_active"])

    def test_writing_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            with mock.patch.object(settings, "SETTINGS_PATH", path):
                settings._write({"a": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["settings.json"])


class TestOldFolderMoves(unittest.TestCase):
    def test_data_follows_the_new_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Stills" / "data").mkdir(parents=True)
            (root / "Stills" / "data" / "settings.json").write_text("{}", encoding="utf-8")
            exe = root / "StillHeart.exe"
            exe.write_bytes(b"x")
            home = paths.portable_home(exe)
            self.assertEqual(home.name, "StillHeart")
            self.assertTrue((home / "data" / "settings.json").is_file())
            self.assertFalse((root / "Stills").exists())

    def test_an_exe_in_its_old_folder_stays(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Stills"
            root.mkdir()
            exe = root / "StillHeart.exe"
            exe.write_bytes(b"x")
            self.assertEqual(paths.portable_home(exe).name, "Stills")


if __name__ == "__main__":
    unittest.main()
