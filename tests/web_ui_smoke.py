from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


SCREENSHOT = Path("test-results/audit-console.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.goto("http://127.0.0.1:8000")
        page.wait_for_load_state("networkidle")

        expect(page.locator("#status")).to_contain_text("2000")
        expect(page.locator("#auditStartBtn")).to_be_enabled()
        page.locator("#auditStartBtn").click()
        expect(page.locator("#auditRecords")).to_contain_text("expected_count", timeout=15000)

        page.locator("#auditSource").select_option("agent_generated")
        page.locator("#auditCount").fill("10")
        page.locator("#auditSeed").fill("20260725")
        page.locator("#auditStartBtn").click()
        expect(page.locator("#auditRecords")).to_contain_text("agent_generated", timeout=15000)
        expect(page.locator("#auditRecords")).to_contain_text("agent_review")

        if os.environ.get("SAVE_SCREENSHOT"):
            SCREENSHOT.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(SCREENSHOT), full_page=True)
        browser.close()
    print("ok: web audit console")


if __name__ == "__main__":
    main()
