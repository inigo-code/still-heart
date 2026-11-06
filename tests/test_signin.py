"""Sign-in: sessions land where gallery-dl reads them, and only for their own site."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine, signin  # noqa: E402
from app import jobs as _jobs  # noqa: E402

# Test rows must never reach the real history file.
_jobs.HISTORY_PATH = Path(tempfile.mkdtemp()) / "history.json"


def _isolated(tmp: Path) -> None:
    signin.SIGNIN_DIR = tmp
    signin.SESSIONS_DIR = tmp / "sessions"
    signin.ACCOUNTS_PATH = tmp / "accounts.json"
    signin.CACHE_PATH = tmp / "cache.sqlite3"
    signin.OAUTH_CONFIG = tmp / "oauth.json"


COOKIE = {"name": "sessionid", "value": "abc", "domain": ".instagram.com", "path": "/", "secure": True, "httponly": True, "expires": 2000000000}


def test_a_session_only_goes_to_its_own_site() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        signin.write_session("instagram", [COOKIE])
        signin._remember("instagram", how="browser")
        assert signin.cookie_file_for("https://www.instagram.com/someone/")
        assert not signin.cookie_file_for("https://notinstagram.com/someone/")
        assert not signin.cookie_file_for("https://instagram.com.evil.example/x")
        assert not signin.cookie_file_for("https://x.com/someone")


def test_gallery_dl_reads_the_session_file() -> None:
    from gallery_dl import util

    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        path = signin.write_session("instagram", [COOKIE, {**COOKIE, "name": "plain", "httponly": False, "domain": "www.instagram.com", "expires": 0}])

        with open(path, encoding="utf-8") as handle:
            jar = util.cookiestxt_load(handle)
        names = {cookie.name: cookie for cookie in jar}
        assert names["sessionid"].value == "abc"
        assert names["sessionid"].domain == ".instagram.com"
        assert names["plain"].domain == "www.instagram.com"


def test_sign_out_removes_the_session() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        path = signin.write_session("instagram", [COOKIE])
        signin._remember("instagram", how="browser")
        signin.sign_out("instagram")
        assert not path.exists()
        assert not signin.cookie_file_for("https://www.instagram.com/x/")
        assert not any(row["signed_in"] for row in signin.status()["sites"])


def test_keys_are_sealed_on_disk_and_reach_the_config() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        signin.save_keys("civitai", {"api-key": "very-secret-value"})
        assert "very-secret-value" not in signin.ACCOUNTS_PATH.read_text(encoding="utf-8")
        payload = {"extractor": {}}
        signin.apply_to_config(payload, "https://civitai.com/models/1")
        assert payload["extractor"]["civitai"]["api-key"] == "very-secret-value"
        other = {"extractor": {}}
        signin.apply_to_config(other, "https://example.com/1")
        assert "civitai" not in other["extractor"]
        assert "sealed" not in json.dumps(signin.status())


def test_a_key_site_needs_every_field() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        try:
            signin.save_keys("bluesky", {"username": "me"})
        except ValueError:
            return
        raise AssertionError("half a sign-in was saved")


def test_pixiv_token_paste_is_kept_as_a_token() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        signin.save_keys("pixiv", {"refresh-token": "tok"})
        payload = {"extractor": {}}
        signin.apply_to_config(payload, "https://www.pixiv.net/artworks/1")
        assert payload["extractor"]["pixiv"]["refresh-token"] == "tok"


def test_job_config_uses_the_saved_session() -> None:
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        _isolated(tmp)
        session = signin.write_session("instagram", [COOKIE])
        signin._remember("instagram", how="browser")
        target = tmp / "job.json"
        engine.write_config(target, {"cookies_browser": "chrome"}, url="https://www.instagram.com/x/")
        data = json.loads(target.read_text(encoding="utf-8"))
        assert Path(data["extractor"]["cookies"]) == session
        assert data["extractor"]["cookies-update"] is False
        assert Path(data["cache"]["file"]) == signin.CACHE_PATH
        engine.write_config(target, {"cookies_browser": "chrome"}, url="https://example.com/x/")
        assert json.loads(target.read_text(encoding="utf-8"))["extractor"]["cookies"] == ["chrome"]


def test_failed_pixiv_job_offers_sign_in() -> None:
    assert signin.needs({"status": "error", "url": "https://www.pixiv.net/artworks/1", "error": "[pixiv][error] 'refresh-token' required."}) == "pixiv"
    assert signin.needs({"status": "done", "url": "https://www.pixiv.net/artworks/1", "error": ""}) == ""
    assert signin.needs({"status": "error", "url": "https://www.pixiv.net/artworks/1", "error": "HTTP 500"}) == ""
    assert "sign in to Pixiv" in engine._friendly_error("[pixiv][error] 'refresh-token' required.")


def test_gallery_dl_prints_the_pixiv_login_address_on_a_pipe() -> None:
    """The window depends on this: the address must arrive while gallery-dl waits for the code."""
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        signin._write_oauth_config()
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "gallery_dl", "--ignore-config", "-c", str(signin.OAUTH_CONFIG), "oauth:pixiv"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONUTF8="1"),
        )
        try:
            seen = ""
            for line in proc.stdout:
                seen += line
                if "app-api.pixiv.net/web/v1/login" in line:
                    break
            assert "code_challenge=" in seen
            assert proc.poll() is None
        finally:
            proc.kill()
            proc.wait()


def test_post_counts_never_become_the_job_total() -> None:
    from app import jobs

    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        job = jobs.create_job("https://www.pixiv.net/en/users/1/stat-test")
        for line in ("1/48", "48/48"):
            engine._consume_download_line(job["id"], line, 0.0, base)
        peek = jobs.peek(job["id"])["progress"]
        assert peek.get("total") is None and peek.get("percent") is None
        assert peek["post_num"] == 48 and peek["post_count"] == 48
        for n in range(3):
            target = base / "pixiv" / "Some artist" / f"{n}_p0.png"
            engine._consume_download_line(job["id"], str(target), 0.0, base)
        peek = jobs.peek(job["id"])
        assert peek["progress"]["saved"] == 3 and peek["progress"]["current"] == 3
        assert peek["title"] == "Some artist"


def test_a_range_is_the_job_total() -> None:
    from app import jobs

    assert engine.range_size("1-80") == 80
    assert engine.range_size("3,7-9") == 4
    assert engine.range_size("") == 0
    assert engine.range_size("5-") == 0
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        job = jobs.create_job("https://example.test/range-test", range_spec="1-4")
        engine._consume_download_line(job["id"], str(base / "a" / "b" / "1.png"), 0.0, base)
        peek = jobs.peek(job["id"])["progress"]
        assert peek["total"] == 4 and peek["percent"] == 25.0


def test_wide_links() -> None:
    assert engine.link_scope("https://www.pixiv.net/en/users/1/following")[0]
    assert engine.link_scope("https://www.pixiv.net/bookmark.php?id=1&type=user")[0]
    assert engine.link_scope("https://www.pixiv.net/en/users/1")[1]
    assert engine.link_scope("https://www.pixiv.net/en/users/1/illustrations?p=2")[1]
    assert engine.link_scope("https://www.pixiv.net/en/artworks/1") == ("", "")
    assert engine.link_scope("https://www.pixiv.net/en/users/1/bookmarks/artworks") == ("", "")


def test_sign_in_says_what_is_required() -> None:
    with tempfile.TemporaryDirectory() as folder:
        _isolated(Path(folder))
        rows = {row["id"]: row["need"] for row in signin.status()["sites"]}
        assert rows["pixiv"] == "required" and rows["instagram"] == "often" and rows["reddit"] == "optional"


if __name__ == "__main__":
    for name, test in sorted(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
