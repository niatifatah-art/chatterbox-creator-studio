from __future__ import annotations

import os

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("CREATOR_STUDIO_BASE_URL", "http://127.0.0.1:7860")


def test_primary_shell_exposes_clear_choices_without_surprise_downloads():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(BASE_URL, wait_until="networkidle")

        expect(page.locator("#product-nav")).to_be_visible()
        expect(page.locator("#script-box textarea")).to_be_visible()
        expect(page.locator("#voice-picker")).to_be_visible()
        expect(page.locator("#model-picker")).to_be_visible()
        expect(page.locator("#language-picker")).to_be_visible()
        expect(page.locator("#generate-btn")).to_be_visible()

        # Compare is an explicit selection UI, not a one-click command that silently
        # downloads every compatible model.
        picker = page.locator("#compare-model-picker")
        expect(picker).to_be_visible()
        labels = " ".join(picker.locator("label").all_inner_texts())
        assert "Multilingual" in labels
        assert "Expressive" in labels
        assert "Light" in labels
        assert "Install first" in labels
        expect(page.locator("#compare-btn")).to_be_disabled()
        expect(page.locator("#compare-status")).to_contain_text("Nothing is downloaded")

        # Models use a persistent selected state rather than an invisible dropdown
        # choice. One model is visibly selected on first load.
        page.get_by_role("tab", name="Models", exact=True).click()
        expect(page.locator("#model-action-picker")).to_be_visible()
        assert page.locator("#model-action-picker input:checked").count() == 1
        expect(page.get_by_text("Models change only when", exact=False)).to_be_visible()

        # Speech-to-text setup is available where Transcribe lives; users are not
        # forced to hunt through Expert settings just to enable the tool.
        page.get_by_role("tab", name="Tools", exact=True).click()
        page.get_by_role("tab", name="Transcribe", exact=True).click()
        expect(page.locator("#transcribe-btn")).to_be_visible()
        expect(page.locator("#install-stt-btn")).to_be_visible()

        browser.close()
