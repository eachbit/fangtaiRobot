from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import Handler


SCREENSHOT = Path("test-results/audit-console.png")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        try:
            page.goto(base_url)
            page.wait_for_load_state("networkidle")

            expect(page.locator("#status")).to_contain_text("2000")
            expect(page.locator("#auditStartBtn")).to_be_enabled()
            page.locator("#auditStartBtn").click()
            expect(page.locator("#auditRecords")).to_contain_text("expected_count", timeout=15000)
            expect(page.locator("#auditOverview")).to_contain_text("官方评分", timeout=15000)

            page.locator("#auditSource").select_option("agent_generated")
            page.locator("#auditCount").fill("10")
            page.locator("#auditSeed").fill("20260725")
            page.locator("#auditStartBtn").click()
            expect(page.locator("#auditRecords")).to_contain_text("agent_generated", timeout=15000)
            expect(page.locator("#auditRecords")).to_contain_text("agent_review")
            expect(page.locator("#auditOverview")).to_contain_text("四项得分")

            if os.environ.get("SAVE_SCREENSHOT"):
                SCREENSHOT.parent.mkdir(exist_ok=True)
                page.screenshot(path=str(SCREENSHOT), full_page=True)
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("ok: web audit console")


if __name__ == "__main__":
    main()
