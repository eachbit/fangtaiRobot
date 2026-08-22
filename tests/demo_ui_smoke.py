from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import Handler


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/demo.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        try:
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="运行当前步骤").click()
            page.wait_for_function("document.querySelector('#metricStatus').textContent === '在线'")
            page.get_by_role("button", name="运行当前步骤").click()
            page.wait_for_function("document.querySelector('#metricDishes').textContent === '4'")
            assert page.locator(".dish-card").count() == 4
            page.get_by_role("button", name="运行当前步骤").click()
            page.wait_for_function("document.querySelector('#changeBadge').textContent.includes('最小修改')")
            assert "蛋" in page.locator("#constraintList").inner_text()
            page.get_by_role("button", name="运行当前步骤").click()
            page.wait_for_function("document.querySelector('#constraintList').textContent.includes('降压')")
            assert "减脂" in page.locator("#constraintList").inner_text()
            assert page.locator(".nutrition-item").count() == 4
            page.get_by_role("button", name="运行当前步骤").click()
            page.wait_for_function("document.querySelector('#changeBadge').textContent.includes('已回滚')")
            assert "v3" in page.locator("#metricVersion").inner_text()
            assert page.locator(".dish-card").count() == 4
            assert "鸡蛋" in page.locator("#constraintList").inner_text()
            assert all("蛋" not in card.inner_text() for card in page.locator(".dish-card").all())

            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(base_url, wait_until="networkidle")
            mobile.get_by_role("button", name="一键运行演示流程").click()
            mobile.wait_for_function(
                "document.querySelector('.step-item[data-step=\"5\"]').classList.contains('done')"
            )
            assert "API 在线" in mobile.locator("#connectionText").inner_text()
            assert "4" in mobile.locator("#metricDishes").inner_text()
            mobile.close()
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("ok: demo UI smoke")


if __name__ == "__main__":
    main()
