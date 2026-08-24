"""
test_price_matrix.py — offline test suite (no network, no Chrome required).

Run:  python -m pytest test_price_matrix.py -v
 or:  python test_price_matrix.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import logging

import automation_core as ac
import chatbot as cb

# Keep test output readable — the engine logs a lot at INFO.
logging.getLogger("price_ledger").setLevel(logging.CRITICAL)
logging.getLogger("price_matrix.app").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Fixtures — trimmed but structurally faithful markup from both stores
# ---------------------------------------------------------------------------
AMAZON_HTML = """
<html><body><div class="s-main-slot">
  <div data-component-type="s-search-result" data-asin="B0CX1234AB" class="puis-card-container">
    <span class="a-color-secondary">Sponsored</span>
    <img class="s-image" src="https://m.media-amazon.com/img1.jpg" alt="HP Victus Gaming Laptop">
    <h2><a class="a-link-normal" href="/HP-Victus/dp/B0CX1234AB/ref=sr_1_1?qid=1699"><span class="a-text-normal">
      HP Victus Gaming Laptop, Intel Core i5-13420H, RTX 4050 6GB, 16GB DDR4 RAM, 512GB SSD, 15.6 inch FHD 144Hz
    </span></a></h2>
    <span class="a-icon-alt">4.3 out of 5 stars</span>
    <span class="a-size-base s-underline-text">1,284</span>
    <span class="a-price"><span class="a-offscreen">₹72,990</span><span class="a-price-whole">72,990</span></span>
    <span class="a-price a-text-price"><span class="a-offscreen">₹94,999</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0CY9999ZZ" class="puis-card-container">
    <img class="s-image" src="https://m.media-amazon.com/img2.jpg" alt="Lenovo IdeaPad">
    <h2><a class="a-link-normal" href="/Lenovo-IdeaPad/dp/B0CY9999ZZ"><span class="a-text-normal">
      Lenovo IdeaPad Slim 3 AMD Ryzen 5 7520U, 8GB LPDDR5 RAM, 512GB SSD, 15.6 inch
    </span></a></h2>
    <span class="a-icon-alt">4.1 out of 5 stars</span>
    <span class="a-size-base s-underline-text">642</span>
    <span class="a-price"><span class="a-offscreen">₹38,490</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0NOPRICE1" class="puis-card-container">
    <h2><span class="a-text-normal">Placeholder card with no price</span></h2>
  </div>
</div></body></html>
"""

FLIPKART_HTML = """
<html><body><div class="_75nlfW">
  <div data-id="COMGZZZZ1">
    <a class="wjcEIp" href="/hp-victus-gaming-i5/p/itm123?pid=COMGZZZZ1&lid=LST&marketplace=FLIPKART"
       title="HP Victus Gaming Laptop Intel Core i5 13th Gen 13420H - (16 GB/512 GB SSD/Windows 11/RTX 4050) 144Hz">
      HP Victus Gaming Laptop</a>
    <img src="https://rukminim.flixcart.com/img1.jpeg" alt="HP Victus">
    <div class="Nx9bqj">₹69,990</div>
    <div class="yRaY8j">₹94,990</div>
    <div class="XQDdHH">4.4</div>
    <span class="Wphh3N">3,021 Ratings &amp; 254 Reviews</span>
  </div>
  <div data-id="COMGZZZZ2">
    <a class="wjcEIp" href="/asus-tuf-gaming/p/itm999?pid=COMGZZZZ2"
       title="ASUS TUF Gaming F15 Core i5 12th Gen 12500H - (16 GB/512 GB SSD/RTX 4050) 144Hz">ASUS TUF</a>
    <div class="Nx9bqj">₹79,990</div>
    <div class="XQDdHH">4.5</div>
    <span class="Wphh3N">1,902 Ratings</span>
  </div>
  <div data-id="COMGNOPRICE">
    <a class="wjcEIp" href="/mystery/p/itm000" title="Item with no price at all">Mystery</a>
  </div>
</div></body></html>
"""


class TestNumberParsing(unittest.TestCase):
    def test_price(self):
        self.assertEqual(ac.parse_price("₹1,23,499.00"), 123499.0)
        self.assertEqual(ac.parse_price("62,990"), 62990.0)
        self.assertEqual(ac.parse_price("Rs. 1499"), 1499.0)
        self.assertIsNone(ac.parse_price("Currently unavailable"))
        self.assertIsNone(ac.parse_price(None))
        self.assertIsNone(ac.parse_price("₹0"))

    def test_rating(self):
        self.assertEqual(ac.parse_rating("4.3 out of 5 stars"), 4.3)
        self.assertEqual(ac.parse_rating("4,2"), 4.2)
        self.assertIsNone(ac.parse_rating("no rating"))
        self.assertIsNone(ac.parse_rating("9.9"))   # out of range

    def test_count(self):
        self.assertEqual(ac.parse_count("1,284 ratings"), 1284)
        self.assertEqual(ac.parse_count("2.3k"), 2300)
        self.assertIsNone(ac.parse_count("none"))

    def test_slugify_is_path_safe(self):
        slug = ac.slugify('usb-c 3.5" jack & more!! /../etc/passwd')
        self.assertNotIn("/", slug)
        self.assertNotIn('"', slug)
        self.assertNotIn("..", slug.replace("-", ""))
        self.assertTrue(slug)


class TestUrls(unittest.TestCase):
    def test_relative_becomes_absolute(self):
        self.assertEqual(ac.make_full_url("/dp/B0X", "amazon"), "https://www.amazon.in/dp/B0X")
        self.assertEqual(ac.make_full_url("dp/B0X", "amazon"), "https://www.amazon.in/dp/B0X")
        self.assertTrue(ac.make_full_url("/p/itm1", "flipkart").startswith("https://www.flipkart.com/"))

    def test_protocol_relative(self):
        self.assertEqual(ac.make_full_url("//cdn.x/y", "amazon"), "https://cdn.x/y")

    def test_tracking_params_stripped(self):
        url = ac.make_full_url("/dp/B0X?ref=sr_1_1&qid=123&th=1&node=976419", "amazon")
        self.assertNotIn("ref=", url)
        self.assertNotIn("qid=", url)
        self.assertIn("node=976419", url)

    def test_fallback_to_search(self):
        url = ac.make_full_url("", "flipkart", "gaming laptop")
        self.assertIn("flipkart.com/search?q=gaming+laptop", url)


class TestSpecs(unittest.TestCase):
    def test_laptop_specs(self):
        s = ac.extract_specs("HP Victus Intel Core i5-13420H RTX 4060 16GB DDR5 RAM 512GB SSD 15.6 inch 144Hz")
        self.assertEqual(s["ram"], "16GB")
        self.assertEqual(s["storage"], "512GB")
        self.assertEqual(s["gpu"], "RTX 4060")
        self.assertEqual(s["refresh"], "144Hz")
        self.assertIn("i5", s["cpu"].lower())

    def test_phone_specs(self):
        s = ac.extract_specs("Redmi Note 13 5G (8GB RAM, 256GB Storage) 5000mAh 120Hz AMOLED")
        self.assertEqual(s["battery"], "5000mAh")
        self.assertEqual(s["refresh"], "120Hz")

    def test_brand_detection(self):
        self.assertEqual(ac.detect_brand("ASUS TUF Gaming F15"), "Asus")
        self.assertEqual(ac.detect_brand("Apple MacBook Air M2"), "Apple")
        self.assertEqual(ac.detect_brand("HP Pavilion 15"), "HP")


class TestAmazonParser(unittest.TestCase):
    def setUp(self):
        self.rows = ac._parse_amazon_page(AMAZON_HTML, page=1, query="gaming laptop")

    def test_skips_unpriced_cards(self):
        self.assertEqual(len(self.rows), 2)

    def test_fields(self):
        r = self.rows[0]
        self.assertEqual(r["platform"], "Amazon")
        self.assertEqual(r["price"], 72990.0)
        self.assertEqual(r["mrp"], 94999.0)
        self.assertEqual(r["rating"], 4.3)
        self.assertEqual(r["reviews"], 1284)
        self.assertEqual(r["product_id"], "B0CX1234AB")
        self.assertTrue(r["sponsored"])
        self.assertEqual(r["link"], "https://www.amazon.in/dp/B0CX1234AB")
        self.assertIn("Victus", r["title"])

    def test_second_row_not_sponsored(self):
        self.assertFalse(self.rows[1]["sponsored"])
        self.assertIsNone(self.rows[1]["mrp"])


class TestFlipkartParser(unittest.TestCase):
    def setUp(self):
        self.rows = ac._parse_flipkart_page(FLIPKART_HTML, page=1, query="gaming laptop")

    def test_skips_unpriced(self):
        titles = [r["title"] for r in self.rows]
        self.assertEqual(len(self.rows), 2, titles)

    def test_fields(self):
        r = self.rows[0]
        self.assertEqual(r["platform"], "Flipkart")
        self.assertEqual(r["price"], 69990.0)
        self.assertEqual(r["mrp"], 94990.0)
        self.assertEqual(r["rating"], 4.4)
        self.assertEqual(r["reviews"], 3021)
        self.assertEqual(r["product_id"], "COMGZZZZ1")
        self.assertTrue(r["link"].startswith("https://www.flipkart.com/"))
        self.assertNotIn("lid=", r["link"])

    def test_no_duplicate_links(self):
        links = [r["link"].split("?")[0] for r in self.rows]
        self.assertEqual(len(links), len(set(links)))


class TestMatching(unittest.TestCase):
    def setUp(self):
        rows = (ac._parse_amazon_page(AMAZON_HTML, 1, "q")
                + ac._parse_flipkart_page(FLIPKART_HTML, 1, "q"))
        self.rows = ac.match_and_score(rows)
        self.by_id = {r.get("product_id"): r for r in self.rows}

    def test_victus_matches_across_stores(self):
        amz = self.by_id["B0CX1234AB"]
        self.assertIn("Victus", amz["match_title"])
        self.assertGreater(amz["match_score"], 0.42)
        self.assertEqual(amz["price_diff"], 72990 - 69990)
        self.assertEqual(amz["cheaper_on"], "Flipkart")

    def test_unmatched_products_get_no_fake_diff(self):
        lenovo = self.by_id["B0CY9999ZZ"]
        # Lenovo has no Flipkart counterpart -> must NOT be diffed against a random item
        self.assertEqual(lenovo["price_diff"], None)
        self.assertEqual(lenovo["match_title"], "")

    def test_value_score_range(self):
        for r in self.rows:
            self.assertGreaterEqual(r["value_score"], 0)
            self.assertLessEqual(r["value_score"], 100)

    def test_discount_pct(self):
        self.assertEqual(self.by_id["B0CX1234AB"]["discount_pct"], 23)
        self.assertIsNone(self.by_id["B0CY9999ZZ"]["discount_pct"])

    def test_different_brands_score_low(self):
        a = {"title": "HP Victus i5 RTX 4050", "brand": "Hp", "price": 70000,
             "_norm": ac.normalize_title("HP Victus i5 RTX 4050"),
             "_tokens": ac.content_tokens("HP Victus i5 RTX 4050"),
             "_models": ac.model_tokens("HP Victus i5 RTX 4050")}
        b = {"title": "Dell Victus i5 RTX 4050", "brand": "Dell", "price": 70000,
             "_norm": ac.normalize_title("Dell Victus i5 RTX 4050"),
             "_tokens": ac.content_tokens("Dell Victus i5 RTX 4050"),
             "_models": ac.model_tokens("Dell Victus i5 RTX 4050")}
        self.assertLess(ac.similarity(a, b), 0.42)


class TestDedupeAndSummary(unittest.TestCase):
    def test_dedupe(self):
        rows = [
            {"platform": "Amazon", "title": "X", "price": 100, "product_id": "A1"},
            {"platform": "Amazon", "title": "X", "price": 100, "product_id": "A1"},
            {"platform": "Amazon", "title": "X", "price": 200, "product_id": "A1"},
        ]
        self.assertEqual(len(ac.dedupe(rows)), 2)

    def test_summarize(self):
        import pandas as pd
        rows = ac.match_and_score(
            ac._parse_amazon_page(AMAZON_HTML, 1, "q") + ac._parse_flipkart_page(FLIPKART_HTML, 1, "q"))
        df = pd.DataFrame(rows)
        s = ac.summarize(df)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["amazon"]["count"], 2)
        self.assertEqual(s["flipkart"]["count"], 2)
        self.assertEqual(s["amazon"]["min"], 38490.0)
        self.assertEqual(s["cheaper_platform"], "Amazon")
        self.assertIsNotNone(s["best_value"])


# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------
ROWS = [
    {"platform": "Amazon", "brand": "HP", "title": "HP Victus RTX 4050 16GB 512GB SSD",
     "price": 72990, "mrp": 94999, "discount_pct": 23, "rating": 4.3, "reviews": 1284,
     "specs": "Core i5 · RTX 4050 · 16GB", "value_score": 61.2, "link": "https://amazon.in/dp/A1",
     "price_diff": 3000, "match_score": 0.81, "cheaper_on": "Flipkart"},
    {"platform": "Amazon", "brand": "Lenovo", "title": "Lenovo IdeaPad Slim 3 Ryzen 5 8GB",
     "price": 38490, "mrp": None, "discount_pct": None, "rating": 4.1, "reviews": 642,
     "specs": "Ryzen 5 · 8GB", "value_score": 70.4, "link": "https://amazon.in/dp/A2",
     "price_diff": None, "match_score": 0, "cheaper_on": ""},
    {"platform": "Flipkart", "brand": "HP", "title": "HP Victus Core i5 RTX 4050 16GB 512GB",
     "price": 69990, "mrp": 94990, "discount_pct": 26, "rating": 4.4, "reviews": 3021,
     "specs": "Core i5 · RTX 4050 · 16GB", "value_score": 74.9, "link": "https://flipkart.com/p/F1",
     "price_diff": -3000, "match_score": 0.81, "cheaper_on": "Flipkart"},
    {"platform": "Flipkart", "brand": "ASUS", "title": "ASUS TUF Gaming F15 RTX 4050 16GB",
     "price": 79990, "mrp": None, "discount_pct": None, "rating": 4.5, "reviews": 1902,
     "specs": "Core i5 · RTX 4050 · 16GB", "value_score": 58.0, "link": "https://flipkart.com/p/F2",
     "price_diff": None, "match_score": 0, "cheaper_on": ""},
]


class TestChatbotParsing(unittest.TestCase):
    def test_budget(self):
        self.assertEqual(cb.parse_budget("cheapest under 50k"), (None, 50000))
        self.assertEqual(cb.parse_budget("under 60"), (None, 60000))
        self.assertEqual(cb.parse_budget("below ₹1,20,000"), (None, 120000))
        self.assertEqual(cb.parse_budget("between 40k and 70k"), (40000, 70000))
        self.assertEqual(cb.parse_budget("above 50000")[0], 50000)
        self.assertEqual(cb.parse_budget("under 1.5 lakh"), (None, 150000))

    def test_specs(self):
        self.assertIn("16gb", cb.parse_specs("show me 16GB laptops"))
        self.assertIn("rtx4050", cb.parse_specs("anything with RTX 4050?"))
        self.assertIn("i5", cb.parse_specs("i5 options"))

    def test_platform(self):
        self.assertEqual(cb.parse_platform("best on flipkart"), "Flipkart")
        self.assertEqual(cb.parse_platform("amazon deals"), "Amazon")
        self.assertIsNone(cb.parse_platform("amazon vs flipkart"))


class TestChatbotAnswers(unittest.TestCase):
    def ask(self, q, rows=ROWS):
        return cb.answer(q, rows=rows)

    def test_cheapest_uses_real_data(self):
        r = self.ask("what's the cheapest option?")
        self.assertTrue(r["used_data"])
        self.assertIn("Lenovo", r["reply"])
        self.assertIn("38,490", r["reply"])

    def test_budget_filter(self):
        r = self.ask("cheapest under 70k")
        self.assertIn("Lenovo", r["reply"])
        self.assertNotIn("79,990", r["reply"])

    def test_platform_filter(self):
        r = self.ask("cheapest on flipkart")
        self.assertIn("69,990", r["reply"])
        self.assertNotIn("38,490", r["reply"])

    def test_compare_platforms(self):
        r = self.ask("which store is cheaper?")
        self.assertIn("Amazon", r["reply"])
        self.assertIn("Flipkart", r["reply"])
        self.assertIn("wins", r["reply"].lower())

    def test_best_rated(self):
        r = self.ask("best rated option")
        self.assertIn("ASUS", r["reply"])

    def test_best_discount(self):
        r = self.ask("biggest discount")
        self.assertIn("26%", r["reply"])

    def test_best_value(self):
        r = self.ask("what should I buy?")
        # highest value_score row (74.9) must be the pick, shown rounded
        self.assertIn("HP Victus Core i5", r["reply"])
        self.assertIn("75/100", r["reply"])
        self.assertIn("Flipkart", r["reply"])

    def test_stats(self):
        r = self.ask("average price")
        self.assertIn("4 listings", r["reply"])
        self.assertIn("median", r["reply"].lower())

    def test_matches(self):
        r = self.ask("biggest price difference between stores")
        self.assertIn("Victus", r["reply"])
        self.assertIn("3,000", r["reply"])

    def test_brand_filter(self):
        r = self.ask("show me hp options")
        self.assertIn("Victus", r["reply"])
        self.assertNotIn("ASUS", r["reply"])

    def test_no_match_is_graceful(self):
        r = self.ask("cheapest under 1000")
        self.assertIn("Nothing", r["reply"])
        self.assertTrue(r["used_data"])

    def test_faq_without_data(self):
        r = cb.answer("how do I export results?", rows=[])
        self.assertFalse(r["used_data"])
        self.assertIn("CSV", r["reply"])

    def test_data_question_without_data(self):
        r = cb.answer("cheapest option", rows=[])
        self.assertIn("results", r["reply"].lower())

    def test_greeting(self):
        self.assertEqual(cb.answer("hi", rows=ROWS)["intent"], "greeting")

    def test_empty(self):
        self.assertEqual(cb.answer("")["intent"], "empty")

    def test_indian_money_format(self):
        self.assertEqual(cb._money(123499), "₹1,23,499")
        self.assertEqual(cb._money(9999), "₹9,999")
        self.assertEqual(cb._money(None), "—")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as flask_app
        cls.app_module = flask_app
        flask_app.app.config["TESTING"] = True
        cls.client = flask_app.app.test_client()

    def test_ping(self):
        r = self.client.get("/api/ping")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_health_reports_chrome(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("scraper_ready", body)
        self.assertIn("chrome", body)

    def test_run_validation(self):
        self.assertEqual(self.client.post("/api/run", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/run", json={"query": "x", "pages": 999}).status_code, 400)
        self.assertEqual(self.client.post("/api/run", json={"query": "x", "pages": "abc"}).status_code, 400)
        r = self.client.post("/api/run", json={"query": "x", "platforms": ["ebay"]})
        self.assertEqual(r.status_code, 400)

    def test_run_accepted_without_chrome_when_fast_path_available(self):
        """Chrome missing is no longer fatal: HTTP can carry the run."""
        if ac.chrome_status()["ready"]:
            self.skipTest("Chrome is available here")
        if not self.app_module.fast_path_available():
            self.skipTest("requests not installed")
        r = self.client.post("/api/run", json={"query": "laptop", "pages": 1})
        self.assertEqual(r.status_code, 202)
        jid = r.get_json()["job_id"]
        self.client.post(f"/api/jobs/{jid}/cancel")

    def test_run_rejected_when_no_fetch_method(self):
        """With both routes gone we must fail fast with 503, not 500."""
        m = self.app_module
        orig = m.fast_path_available
        m.fast_path_available = lambda: False
        try:
            if ac.chrome_status()["ready"]:
                self.skipTest("Chrome is available here")
            r = self.client.post("/api/run", json={"query": "laptop", "pages": 1})
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.get_json()["error_type"], "driver")
        finally:
            m.fast_path_available = orig

    def test_health_reports_both_routes(self):
        h = self.client.get("/api/health").get_json()
        self.assertIn("fast_path", h)
        self.assertIn("browser_fallback", h)
        self.assertIn("page_workers", h)

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/api/status/deadbeef").status_code, 404)
        self.assertEqual(self.client.get("/api/download/deadbeef").status_code, 404)
        self.assertEqual(self.client.post("/api/jobs/deadbeef/cancel").status_code, 404)

    def test_chat_endpoint(self):
        r = self.client.post("/api/chat", json={"message": "cheapest option", "rows": ROWS})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["used_data"])
        self.assertIn("Lenovo", body["reply"])
        self.assertEqual(body["context_rows"], 4)

    def test_chat_requires_message(self):
        self.assertEqual(self.client.post("/api/chat", json={}).status_code, 400)

    def test_chat_suggestions(self):
        r = self.client.get("/api/chat/suggestions")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.get_json()["suggestions"]), 0)

    def test_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Price Matrix", r.data)

    def test_export_flow_with_synthetic_job(self):
        """Inject a finished job and check CSV / XLSX / JSON exports."""
        import pandas as pd, threading, time as _t
        m = self.app_module
        job = m._new_job("testjob01", "gaming laptop", 1, ["amazon", "flipkart"])
        rows = ac.match_and_score(
            ac._parse_amazon_page(AMAZON_HTML, 1, "q") + ac._parse_flipkart_page(FLIPKART_HTML, 1, "q"))
        df = pd.DataFrame(rows)
        job.update({"status": "done", "data": json.loads(df.to_json(orient="records")),
                    "row_count": len(df), "stats": ac.summarize(df), "progress": 100})
        with m.LOCK:
            m.JOBS["testjob01"] = job

        r = self.client.get("/api/status/testjob01")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["row_count"], 4)

        r = self.client.get("/api/download/testjob01?format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Victus", r.data.decode("utf-8", "replace"))

        r = self.client.get("/api/download/testjob01?format=json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()["rows"]), 4)

        r = self.client.get("/api/download/testjob01?format=xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.data), 4000)
        self.assertEqual(r.data[:2], b"PK")   # valid zip/xlsx magic

        r = self.client.get("/api/jobs")
        self.assertIn("testjob01", [j["job_id"] for j in r.get_json()["jobs"]])

        self.assertEqual(self.client.delete("/api/jobs/testjob01").status_code, 200)

    def test_chat_reads_live_job_data(self):
        m = self.app_module
        job = m._new_job("chatjob01", "laptop", 1, ["amazon"])
        job.update({"status": "done", "data": ROWS, "row_count": len(ROWS)})
        with m.LOCK:
            m.JOBS["chatjob01"] = job
        r = self.client.post("/api/chat", json={"message": "which store is cheaper?", "job_id": "chatjob01"})
        body = r.get_json()
        self.assertTrue(body["used_data"])
        self.assertEqual(body["context_rows"], 4)
        self.client.delete("/api/jobs/chatjob01")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# v3: fetch ordering, concurrency and driver reuse
# ---------------------------------------------------------------------------
class _FakeDriver:
    """Minimal stand-in for a Selenium driver."""
    counter = 0
    boots = []

    def __init__(self):
        _FakeDriver.counter += 1
        self.id = _FakeDriver.counter
        _FakeDriver.boots.append(self.id)
        self.alive = True

    def get(self, url): pass
    def quit(self): self.alive = False
    def set_page_load_timeout(self, t): pass
    def implicitly_wait(self, t): pass
    def execute_cdp_cmd(self, *a, **k): pass
    def execute_script(self, *a, **k): pass
    def find_element(self, *a, **k): raise RuntimeError("no element")
    @property
    def page_source(self): return ""


class FetchStrategyTests(unittest.TestCase):
    """Exercises the real scrape() orchestration with network/browser stubbed."""

    def setUp(self):
        self._orig = (ac._fetch_http, ac._build_driver, ac._fetch_page, ac.chrome_status)
        _FakeDriver.counter = 0
        _FakeDriver.boots = []
        self.events = []
        self.lock = threading.Lock()
        ac._build_driver = lambda headless, timeout=30: _FakeDriver()
        ac.chrome_status = lambda: {"selenium_available": True, "chrome_found": True,
                                    "chrome_path": "/x", "chrome_version": "Chrome (stub)",
                                    "ready": True, "selenium_error": ""}

    def tearDown(self):
        ac._fetch_http, ac._build_driver, ac._fetch_page, ac.chrome_status = self._orig

    def _record(self, ev, **kw):
        with self.lock:
            self.events.append(dict(t=time.time(), ev=ev, **kw))

    def _install(self, http_fails_for=()):
        def http(url, referer=""):
            store = "amazon" if "amazon" in url else "flipkart"
            self._record("http", store=store)
            time.sleep(0.05)
            if store in http_fails_for:
                return None, "bot-wall"
            return (AMAZON_HTML if store == "amazon" else FLIPKART_HTML), "ok"

        def page(driver, url, wait_css, cfg, log, label):
            store = "amazon" if "amazon" in url else "flipkart"
            self._record("selenium", store=store, driver=driver.id)
            time.sleep(0.05)
            return AMAZON_HTML if store == "amazon" else FLIPKART_HTML

        ac._fetch_http = http
        ac._fetch_page = page

    def _run(self, pages=4, **kw):
        cfg = ac.ScrapeConfig(query="gaming laptop", pages=pages,
                              output_dir=tempfile.mkdtemp())
        return ac.scrape(cfg, log=lambda m: None, **kw)

    def test_amazon_completes_before_flipkart_starts(self):
        self._install()
        self._run(pages=4)
        amz = [e["t"] for e in self.events if e["store"] == "amazon"]
        fk = [e["t"] for e in self.events if e["store"] == "flipkart"]
        self.assertTrue(amz and fk)
        self.assertLessEqual(max(amz), min(fk),
                             "Flipkart must not start until Amazon has finished")

    def test_pages_within_a_store_are_concurrent(self):
        """Wall-clock must beat the serial baseline, not just look bunched up."""
        orig_jitter = ac.INTRA_STORE_DELAY
        ac.INTRA_STORE_DELAY = 0.0          # remove randomness from the measurement
        try:
            def slow_http(url, referer=""):
                store = "amazon" if "amazon" in url else "flipkart"
                self._record("http", store=store)
                time.sleep(0.20)            # 4 pages serially = 0.80s per store
                return (AMAZON_HTML if store == "amazon" else FLIPKART_HTML), "ok"
            ac._fetch_http = slow_http
            ac._fetch_page = lambda *a, **k: AMAZON_HTML

            pages = 4
            t0 = time.time()
            self._run(pages=pages)
            elapsed = time.time() - t0

            serial = 0.20 * pages * 2       # both stores, one page at a time
            self.assertLess(elapsed, serial * 0.6,
                            f"expected concurrency: took {elapsed:.2f}s vs serial {serial:.2f}s")
        finally:
            ac.INTRA_STORE_DELAY = orig_jitter

    def test_http_fast_path_never_boots_chrome(self):
        self._install()
        self._run(pages=4)
        self.assertEqual(_FakeDriver.boots, [],
                         "Chrome must not start when the HTTP path succeeds")

    def test_selenium_fallback_when_http_blocked(self):
        self._install(http_fails_for=("amazon", "flipkart"))
        res = self._run(pages=3)
        self.assertGreater(len(_FakeDriver.boots), 0, "should fall back to a browser")
        self.assertFalse(res.df.empty)

    def test_drivers_are_shared_across_stores(self):
        self._install(http_fails_for=("amazon", "flipkart"))
        self._run(pages=3)
        used = {e["driver"] for e in self.events if e["ev"] == "selenium"}
        # 3 pages x 2 stores = 6 fetches, but the pool caps drivers at PAGE_WORKERS
        # and reuses them for BOTH stores rather than rebooting per store.
        self.assertLessEqual(len(_FakeDriver.boots), min(ac.PAGE_WORKERS, 3))
        self.assertLessEqual(len(used), min(ac.PAGE_WORKERS, 3))

    def test_output_page_order_is_deterministic(self):
        self._install()
        res = self._run(pages=4)
        amz = res.df[res.df["platform"] == "Amazon"]["page"].tolist()
        self.assertEqual(amz, sorted(amz), "rows must be ordered by page")

    def test_partial_callback_fires_per_store_in_order(self):
        self._install()
        seen = []
        self._run(pages=2, on_partial=lambda store, rows: seen.append(store))
        self.assertEqual(seen, ["Amazon", "Flipkart"])

    def test_partial_rows_are_analysed(self):
        self._install()
        captured = {}
        self._run(pages=2, on_partial=lambda s, r: captured.setdefault(s, r))
        row = captured["Amazon"][0]
        for key in ("value_score", "brand", "specs", "price_diff_label"):
            self.assertIn(key, row)

    def test_runs_without_chrome_when_http_works(self):
        """Chrome missing is no longer fatal — the fast path can carry the run."""
        self._install()
        ac.chrome_status = lambda: {"selenium_available": False, "chrome_found": False,
                                    "chrome_path": "", "chrome_version": "",
                                    "ready": False, "selenium_error": "not installed"}
        res = self._run(pages=2)
        self.assertFalse(res.df.empty)
        self.assertEqual(_FakeDriver.boots, [])

    def test_cancellation_keeps_partial_results(self):
        self._install()
        flag = {"v": False}
        def cancel():
            return flag["v"]
        def trip():
            time.sleep(0.12); flag["v"] = True
        threading.Thread(target=trip, daemon=True).start()
        res = self._run(pages=8, should_cancel=cancel)
        self.assertTrue(res.cancelled)

    def test_sequential_order_follows_platforms_argument(self):
        self._install()
        cfg = ac.ScrapeConfig(query="x", pages=2, platforms=("flipkart", "amazon"),
                              output_dir=tempfile.mkdtemp())
        ac.scrape(cfg, log=lambda m: None)
        fk = [e["t"] for e in self.events if e["store"] == "flipkart"]
        amz = [e["t"] for e in self.events if e["store"] == "amazon"]
        self.assertLessEqual(max(fk), min(amz), "explicit platform order must win")


class HttpFastPathTests(unittest.TestCase):
    def test_rejects_bot_wall(self):
        import automation_core as m
        wall = "<html><body>" + ("Enter the characters you see below " * 50) + "</body></html>"
        self.assertTrue(m._looks_blocked(wall))

    def test_rejects_tiny_response(self):
        class R:
            status_code = 200
            text = "<html>nope</html>"
        import automation_core as m
        orig = m._http_session
        m._http_session = lambda: type("S", (), {"get": lambda self, *a, **k: R()})()
        try:
            html, reason = m._fetch_http("https://www.amazon.in/s?k=x")
            self.assertIsNone(html)
            self.assertEqual(reason, "too-small")
        finally:
            m._http_session = orig

    def test_rejects_non_200(self):
        class R:
            status_code = 503
            text = "x" * 30000
        import automation_core as m
        orig = m._http_session
        m._http_session = lambda: type("S", (), {"get": lambda self, *a, **k: R()})()
        try:
            html, reason = m._fetch_http("https://www.amazon.in/s?k=x")
            self.assertIsNone(html)
            self.assertEqual(reason, "http-503")
        finally:
            m._http_session = orig


# ---------------------------------------------------------------------------
# Codespaces / dev-container compatibility
# ---------------------------------------------------------------------------
class CodespacesTests(unittest.TestCase):
    """The app must bind and advertise itself correctly inside a cloud IDE."""

    ENV_KEYS = ("CODESPACES", "CODESPACE_NAME", "GITPOD_WORKSPACE_ID",
                "GITPOD_WORKSPACE_URL", "REMOTE_CONTAINERS", "PM_HOST",
                "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")

    def setUp(self):
        import app as m
        self.m = m
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _host(self):
        """Mirror the host-resolution logic in app.__main__."""
        return os.environ.get("PM_HOST") or ("0.0.0.0" if self.m._in_container_ide() else "127.0.0.1")

    def test_local_defaults_to_loopback(self):
        self.assertFalse(self.m._in_container_ide())
        self.assertEqual(self._host(), "127.0.0.1")

    def test_codespaces_binds_all_interfaces(self):
        """Loopback would be invisible to the port forwarder."""
        os.environ["CODESPACES"] = "true"
        self.assertTrue(self.m._in_container_ide())
        self.assertEqual(self._host(), "0.0.0.0")

    def test_gitpod_and_devcontainer_detected(self):
        for key in ("GITPOD_WORKSPACE_ID", "REMOTE_CONTAINERS"):
            os.environ.pop("CODESPACES", None)
            os.environ[key] = "1"
            self.assertTrue(self.m._in_container_ide(), key)
            os.environ.pop(key)

    def test_explicit_pm_host_wins(self):
        os.environ["CODESPACES"] = "true"
        os.environ["PM_HOST"] = "127.0.0.1"
        self.assertEqual(self._host(), "127.0.0.1")

    def test_public_url_for_codespaces(self):
        os.environ["CODESPACE_NAME"] = "curly-space-happiness"
        self.assertEqual(self.m._public_url(8787),
                         "https://curly-space-happiness-8787.app.github.dev")

    def test_public_url_honours_custom_domain(self):
        os.environ["CODESPACE_NAME"] = "abc"
        os.environ["GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"] = "preview.app.github.dev"
        self.assertEqual(self.m._public_url(8787),
                         "https://abc-8787.preview.app.github.dev")

    def test_public_url_for_gitpod(self):
        os.environ["GITPOD_WORKSPACE_URL"] = "https://myws.gitpod.io"
        self.assertEqual(self.m._public_url(8787), "https://8787-myws.gitpod.io")

    def test_no_public_url_when_local(self):
        self.assertEqual(self.m._public_url(8787), "")


class DevcontainerConfigTests(unittest.TestCase):
    ROOT = os.path.dirname(os.path.abspath(__file__))

    def _config(self):
        path = os.path.join(self.ROOT, ".devcontainer", "devcontainer.json")
        raw = open(path, encoding="utf-8").read()
        return json.loads(re.sub(r"^\s*//.*$", "", raw, flags=re.M))

    def test_files_exist(self):
        for rel in (".devcontainer/devcontainer.json", ".devcontainer/setup.sh"):
            self.assertTrue(os.path.exists(os.path.join(self.ROOT, rel)), rel)

    def test_config_is_valid_jsonc(self):
        cfg = self._config()
        self.assertIn("image", cfg)

    def test_forwards_the_app_port(self):
        cfg = self._config()
        self.assertIn(8787, cfg["forwardPorts"])
        self.assertEqual(cfg["portsAttributes"]["8787"]["protocol"], "http")

    def test_binds_all_interfaces_via_env(self):
        self.assertEqual(self._config()["containerEnv"]["PM_HOST"], "0.0.0.0")

    def test_setup_script_is_executable_and_valid(self):
        path = os.path.join(self.ROOT, ".devcontainer", "setup.sh")
        self.assertTrue(os.access(path, os.X_OK), "setup.sh must be executable")
        rc = subprocess.run(["bash", "-n", path], capture_output=True)
        self.assertEqual(rc.returncode, 0, rc.stderr.decode())

    def test_setup_installs_requirements(self):
        body = open(os.path.join(self.ROOT, ".devcontainer", "setup.sh"),
                    encoding="utf-8").read()
        self.assertIn("requirements.txt", body)
        # Chrome is optional: its failure must not abort the container build.
        self.assertNotIn("set -e\n", body)


# ---------------------------------------------------------------------------
# Health detection must not go stale (Codespaces installs Chrome in background)
# ---------------------------------------------------------------------------
class HealthFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._bin = ac._chrome_binary
        self._sel = ac.SELENIUM_AVAILABLE
        self._err = ac.SELENIUM_IMPORT_ERROR
        self._ttl = ac._STATUS_TTL
        ac._STATUS_CACHE.update({"at": 0.0, "value": None})

    def tearDown(self):
        ac._chrome_binary = self._bin
        ac.SELENIUM_AVAILABLE = self._sel
        ac.SELENIUM_IMPORT_ERROR = self._err
        ac._STATUS_TTL = self._ttl
        ac._STATUS_CACHE.update({"at": 0.0, "value": None})

    def test_chrome_installed_after_boot_is_detected(self):
        """The exact Codespaces case: app starts, Chrome arrives later."""
        ac._STATUS_TTL = 0.05
        ac._chrome_binary = lambda: None
        self.assertFalse(ac.chrome_status()["chrome_found"])

        ac._chrome_binary = lambda: "/usr/bin/google-chrome"
        time.sleep(0.08)                      # let the short TTL lapse
        self.assertTrue(ac.chrome_status()["chrome_found"],
                        "status must refresh once Chrome appears")

    def test_force_bypasses_cache(self):
        ac._chrome_binary = lambda: None
        ac.chrome_status()
        ac._chrome_binary = lambda: "/usr/bin/google-chrome"
        self.assertFalse(ac.chrome_status()["chrome_found"], "cached")
        self.assertTrue(ac.chrome_status(force=True)["chrome_found"], "forced")

    def test_cache_avoids_repeated_subprocess_calls(self):
        calls = {"n": 0}
        def counting():
            calls["n"] += 1
            return None
        ac._chrome_binary = counting
        for _ in range(5):
            ac.chrome_status()
        self.assertEqual(calls["n"], 1, "health polling must not spawn per request")

    def test_selenium_reimported_when_installed_later(self):
        ac.SELENIUM_AVAILABLE = False
        ac.SELENIUM_IMPORT_ERROR = "No module named 'selenium'"
        try:
            import selenium  # noqa: F401
        except Exception:
            self.skipTest("selenium genuinely unavailable here")
        self.assertTrue(ac._selenium_available())
        self.assertTrue(ac.chrome_status(force=True)["selenium_available"])

    def test_health_endpoint_reflects_recovery(self):
        import app as m
        m.app.config["TESTING"] = True
        client = m.app.test_client()
        ac._STATUS_TTL = 0.05
        ac._chrome_binary = lambda: None
        m.chrome_status = ac.chrome_status
        self.assertFalse(client.get("/api/health").get_json()["browser_fallback"])

        ac._chrome_binary = lambda: "/usr/bin/google-chrome"
        time.sleep(0.08)
        self.assertTrue(client.get("/api/health").get_json()["browser_fallback"],
                        "/api/health must stop reporting 'fast mode only'")


class HealthBannerMarkupTests(unittest.TestCase):
    """The frontend must be able to recover the banner without a reload."""

    @classmethod
    def setUpClass(cls):
        cls.html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "index.html"), encoding="utf-8").read()

    def test_has_recheck_button(self):
        self.assertIn("healthRetry", self.html)

    def test_repolls_while_degraded(self):
        self.assertIn("scheduleHealthRecheck", self.html)

    def test_rechecks_on_tab_focus(self):
        self.assertIn("visibilitychange", self.html)

    def test_no_longer_claims_chrome_missing_permanently(self):
        # Old copy told users to install Chrome then restart, with no recovery.
        self.assertNotIn("install Chrome to enable the browser fallback", self.html)
