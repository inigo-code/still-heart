import json
import unittest

from pathlib import Path

from app.engine import (
    _filter_expression,
    _under_base,
    compress_range,
    item_from_message,
    parse_dump,
    parse_dump_errors,
    parse_extractors,
    range_start,
    simplify_item,
)
from app.main import MAX_BATCH, parse_urls


class TestRange(unittest.TestCase):
    def test_range_start(self):
        self.assertEqual(range_start(""), 1)
        self.assertEqual(range_start("   "), 1)
        self.assertEqual(range_start("7-40"), 7)
        self.assertEqual(range_start("12"), 12)
        self.assertEqual(range_start("3,9-12"), 3)
        self.assertEqual(range_start("junk"), 1)
        self.assertEqual(range_start("0-5"), 1)

    def test_compress_range(self):
        self.assertEqual(compress_range([]), "")
        self.assertEqual(compress_range([5]), "5")
        self.assertEqual(compress_range([1, 2, 3]), "1-3")
        self.assertEqual(compress_range([1, 3, 4, 5, 9, 2]), "1-5,9")
        self.assertEqual(compress_range([0, -3, 7]), "7")
        self.assertEqual(compress_range([10, 10, 11]), "10-11")


class TestItems(unittest.TestCase):
    def test_simplify_dict(self):
        item = simplify_item(
            {"url": "http://x/1.jpg", "extension": ".jpg", "width": 800, "height": 600, "filename": "pic"},
            3,
        )
        self.assertEqual(item["extension"], "jpg")
        self.assertEqual(item["kind"], "image")
        self.assertEqual(item["width"], 800)
        self.assertEqual(item["index"], 3)

    def test_simplify_tuple_form(self):
        item = simplify_item(("http://x/1.mp4", {"extension": "mp4"}), 1)
        self.assertEqual(item["url"], "http://x/1.mp4")
        self.assertEqual(item["kind"], "video")

    def test_simplify_non_dict(self):
        self.assertIsNone(simplify_item("nope", 1))
        self.assertIsNone(simplify_item(None, 1))

    def test_simplify_fallback_filename(self):
        item = simplify_item({"url": "http://x/z.png"}, 9)
        self.assertEqual(item["filename"], "file-9")

    def test_simplify_thumb_from_image(self):
        item = simplify_item({"url": "http://x/z.png", "extension": "png"}, 1)
        self.assertEqual(item["thumb"], "http://x/z.png")

    def test_message_minus_one_is_error(self):
        self.assertIsNone(item_from_message([-1, {"message": "boom"}], 1))

    def test_message_directory_skipped(self):
        self.assertIsNone(item_from_message([2, "http://x/"], 1))

    def test_message_file_tuple(self):
        item = item_from_message([3, "http://x/a.jpg", {"extension": "jpg", "width": 400}], 4)
        self.assertEqual(item["url"], "http://x/a.jpg")
        self.assertEqual(item["width"], 400)
        self.assertEqual(item["index"], 4)

    def test_message_plain_dict(self):
        item = item_from_message({"url": "http://x/b.gif", "extension": "gif"}, 2)
        self.assertEqual(item["kind"], "gif")


class TestUnderBase(unittest.TestCase):
    def test_accepts_either_slash_and_rejects_a_sibling_prefix(self):
        base = Path("C:/Downloads/stills")
        self.assertTrue(_under_base(base, Path("C:/Downloads/stills/pixiv/a.jpg")))
        self.assertTrue(_under_base(base, Path(r"C:\Downloads\stills\pixiv\a.jpg")))
        self.assertFalse(_under_base(base, Path("C:/Downloads/stills-extra/a.jpg")))
        self.assertFalse(_under_base(base, Path("C:/Other/a.jpg")))


class TestParseDump(unittest.TestCase):
    def test_parse_list(self):
        data = [
            [2, "http://x/dir/"],
            [3, "http://x/1.jpg", {"extension": "jpg"}],
            {"url": "http://x/2.png", "extension": "png"},
        ]
        items = parse_dump(json.dumps(data))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["url"], "http://x/1.jpg")
        self.assertEqual(items[1]["extension"], "png")

    def test_parse_jsonl_fallback(self):
        text = "noise line\n" + json.dumps([3, "http://x/1.jpg", {"extension": "jpg"}])
        items = parse_dump(text)
        self.assertEqual(len(items), 1)

    def test_parse_single_dict(self):
        items = parse_dump(json.dumps({"url": "http://x/1.webp", "extension": "webp"}))
        self.assertEqual(len(items), 1)

    def test_parse_empty(self):
        self.assertEqual(parse_dump(""), [])
        self.assertEqual(parse_dump("not json at all {{{"), [])

    def test_parse_dump_errors(self):
        data = [[-1, {"message": "boom"}], [3, "http://x/1.jpg", {"extension": "jpg"}]]
        self.assertEqual(parse_dump_errors(json.dumps(data)), ["boom"])
        self.assertEqual(parse_dump_errors(json.dumps([{"url": "x"}])), [])


class TestFilters(unittest.TestCase):
    def test_filter_all(self):
        self.assertEqual(_filter_expression({"filter_mode": "all"}), "")

    def test_filter_images(self):
        expr = _filter_expression({"filter_mode": "images"})
        self.assertIn("extension in", expr)
        self.assertIn("jpg", expr)

    def test_filter_videos_min_width(self):
        expr = _filter_expression({"filter_mode": "videos", "min_width": 1920})
        self.assertIn("width >= 1920", expr)
        self.assertIn("mp4", expr)


class TestParseUrls(unittest.TestCase):
    def test_split_and_dedupe(self):
        urls = parse_urls("http://a/1 http://a/1,https://b/2\n\nhttps://b/2")
        self.assertEqual(urls, ["http://a/1", "https://b/2"])

    def test_split_is_case_insensitive(self):
        self.assertEqual(
            parse_urls("https://a/1,HTTP://b/2"),
            ["https://a/1", "HTTP://b/2"],
        )

    def test_rejects_non_http(self):
        self.assertEqual(parse_urls("javascript:alert(1) data:text/html,x file:///C:/win"), [])
        self.assertEqual(parse_urls("notaurl"), [])

    def test_cap(self):
        raw = " ".join(f"http://a/{i}" for i in range(200))
        self.assertEqual(len(parse_urls(raw)), MAX_BATCH)


class TestExtractors(unittest.TestCase):
    def test_parse_extractors(self):
        text = """
Category: pixiv, Subcategory: user
Example: https://www.pixiv.net/en/users/173530

Category: imgur
Example: https://imgur.com/album/xyz
"""
        items = parse_extractors(text)
        labels = [i["label"] for i in items]
        self.assertIn("pixiv:user", labels)
        self.assertIn("imgur", labels)

    def test_dedupe(self):
        items = parse_extractors("Category: a\n\nCategory: a\n")
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
