from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def verify(url: str, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, object] = {"url": url, "viewports": {}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, viewport in (
            ("desktop", {"width": 1440, "height": 1000}),
            ("mobile", {"width": 390, "height": 844}),
        ):
            page = browser.new_page(viewport=viewport)
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector("#stageList .stage-item")
            stage_count = page.locator("#stageList .stage-item").count()
            if stage_count != 8:
                raise AssertionError(f"expected 8 pipeline stages, got {stage_count}")
            dimensions = page.evaluate(
                """() => ({
                    bodyWidth: document.body.scrollWidth,
                    viewportWidth: window.innerWidth,
                    bodyHeight: document.body.scrollHeight,
                    title: document.title
                })"""
            )
            if dimensions["bodyWidth"] > dimensions["viewportWidth"] + 1:
                raise AssertionError(f"page overflows horizontally at {name}: {dimensions}")
            if dimensions["bodyHeight"] < viewport["height"] // 2:
                raise AssertionError(f"page appears blank at {name}: {dimensions}")
            if name == "mobile":
                start_box = page.locator("#startButton").bounding_box()
                cancel_box = page.locator("#cancelButton").bounding_box()
                if not start_box or not cancel_box or start_box["x"] + start_box["width"] > cancel_box["x"]:
                    raise AssertionError("mobile command buttons overlap")
            screenshot = output_dir / f"capture-ui-{name}.png"
            page.screenshot(path=str(screenshot), full_page=True)
            result["viewports"][name] = {
                "stage_count": stage_count,
                "dimensions": dimensions,
                "screenshot": str(screenshot),
            }
            page.close()

        page = browser.new_page(viewport={"width": 1024, "height": 768})
        page.goto(url, wait_until="networkidle")
        page.fill("#postUrl", "https://example.com/not-facebook")
        page.click("#startButton")
        error = page.locator("#formError").inner_text()
        if "Facebook" not in error:
            raise AssertionError(f"invalid URL was not rejected locally: {error!r}")
        result["invalid_url_rejected"] = True
        page.close()
        browser.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766/")
    parser.add_argument("--output-dir", type=Path, default=Path("evidence/capture-ui"))
    args = parser.parse_args()
    print(json.dumps(verify(args.url, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
