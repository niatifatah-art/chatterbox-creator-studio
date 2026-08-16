from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


BASE_URL = os.environ.get("CREATOR_STUDIO_BASE_URL", "http://127.0.0.1:7860")
ARTIFACT_DIR = Path(os.environ.get("UI_ARTIFACT_DIR", "ui-artifacts"))


def _button(page, selector: str):
    root = page.locator(selector)
    return root if root.evaluate("el => el.tagName.toLowerCase() === 'button'") else root.locator("button")


def _shot(page, name: str) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACT_DIR / name), full_page=True)


def test_primary_shell_exposes_clear_choices_without_surprise_downloads():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        # Gradio keeps long-lived browser connections. Playwright explicitly
        # discourages networkidle for readiness assertions; wait for the document,
        # then assert the product surface we actually care about.
        page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(page.locator("#product-nav")).to_be_visible(timeout=20_000)

        expect(page.locator("#script-box textarea")).to_be_visible()
        expect(page.locator("#voice-picker")).to_be_visible()
        expect(page.locator("#model-picker")).to_be_visible()
        expect(page.locator("#language-picker")).to_be_visible()
        expect(_button(page, "#generate-btn")).to_be_visible()
        _shot(page, "01-create.png")

        # Pause shortcuts are actions, not mysterious persistent modes. They insert
        # at the text caret rather than silently appending at the end of the script.
        script = page.locator("#script-box textarea")
        script.fill("Hello world")
        script.evaluate("el => { el.focus(); el.setSelectionRange(5, 5); }")
        page.get_by_role("button", name="+ 0.5s pause", exact=True).click()
        expect(script).to_have_value("Hello [pause=0.5] world")

        # Compare is an explicit selection UI, not a one-click command that silently
        # downloads every compatible model. A clean CI cache has no installed models.
        picker = page.locator("#compare-model-picker")
        expect(picker).to_be_visible()
        labels = " ".join(picker.locator("label").all_inner_texts())
        assert "Multilingual" in labels
        assert "Expressive" in labels
        assert "Light" in labels
        assert "Install first" in labels
        expect(_button(page, "#compare-btn")).to_be_disabled()
        expect(page.locator("#compare-status")).to_contain_text("Nothing is downloaded")

        # The selection itself remains visible and interactive even though Compare
        # stays disabled until the missing models are intentionally installed.
        multilingual = picker.locator("label", has_text="Multilingual")
        light = picker.locator("label", has_text="Light")
        multilingual.click()
        light.click()
        expect(multilingual.locator("input")).to_be_checked()
        expect(light.locator("input")).to_be_checked()
        expect(page.locator("#compare-status")).to_contain_text("Install", timeout=10_000)
        expect(_button(page, "#compare-btn")).to_be_disabled()
        _shot(page, "02-compare-selected.png")

        # Models use a persistent selected state rather than an invisible dropdown
        # choice, and changing the choice visibly updates the checked control.
        page.get_by_role("tab", name="Models", exact=True).click()
        expect(page.locator("#model-action-picker")).to_be_visible()
        assert page.locator("#model-action-picker input:checked").count() == 1
        expressive = page.locator("#model-action-picker label", has_text="Expressive")
        expressive.click()
        expect(expressive.locator("input")).to_be_checked()
        expect(page.get_by_text("Models change only when", exact=False)).to_be_visible()
        _shot(page, "03-models.png")

        # Speech-to-text setup is available where Transcribe lives; users are not
        # forced to hunt through Expert settings just to enable the tool.
        page.get_by_role("tab", name="Tools", exact=True).click()
        page.get_by_role("tab", name="Transcribe", exact=True).click()
        expect(_button(page, "#transcribe-btn")).to_be_visible()
        expect(_button(page, "#install-stt-btn")).to_be_visible()
        _shot(page, "04-transcribe.png")

        browser.close()
