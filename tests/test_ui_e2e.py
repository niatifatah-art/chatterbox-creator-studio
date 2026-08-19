from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


BASE_URL = os.environ.get("CREATOR_STUDIO_BASE_URL", "http://127.0.0.1:7860")
ARTIFACT_DIR = Path(os.environ.get("UI_ARTIFACT_DIR", "ui-artifacts"))
BROWSER_CHANNEL = (os.environ.get("CREATOR_STUDIO_BROWSER_CHANNEL") or "").strip() or None


def _button(page, selector: str):
    root = page.locator(selector)
    return root if root.evaluate("el => el.tagName.toLowerCase() === 'button'") else root.locator("button")


def _shot(page, name: str) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACT_DIR / name), full_page=True)


def _choose_dropdown(page, root_selector: str, label: str) -> None:
    # Gradio 6 renders both its text input and the floating choices container with
    # role=listbox. Target the actual input explicitly, then use keyboard selection.
    listbox = page.locator(root_selector).locator('input[role="listbox"]')
    expect(listbox).to_be_visible()
    listbox.click()
    listbox.fill(label)
    page.keyboard.press("Enter")
    expect(listbox).to_have_value(label, timeout=5_000)


def _check_choice(group, accessible_name: str):
    # Use the checkbox's semantic role instead of clicking Gradio's wrapping label.
    # Playwright's checkbox action waits for actionability and verifies the final state,
    # which is much less fragile across Gradio/Svelte DOM re-renders.
    choice = group.get_by_role("checkbox", name=accessible_name, exact=False)
    choice.check()
    expect(choice).to_be_checked()
    return choice


def test_primary_shell_is_explicit_adaptive_and_calm():
    with sync_playwright() as playwright:
        launch_options = {"channel": BROWSER_CHANNEL} if BROWSER_CHANNEL else {}
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(page.locator("#product-nav")).to_be_visible(timeout=20_000)

        # The common path stays compact: script, voice, model, language when useful,
        # and one primary Generate action.
        expect(page.locator("#script-box textarea")).to_be_visible()
        expect(page.locator("#voice-picker")).to_be_visible()
        expect(page.locator("#model-picker")).to_be_visible()
        expect(page.locator("#language-picker")).to_be_visible()
        expect(_button(page, "#generate-btn")).to_be_visible()
        _shot(page, "01-create.png")

        # Exact pauses are editor actions and insert at the caret.
        script = page.locator("#script-box textarea")
        script.fill("Hello world")
        script.evaluate("el => { el.focus(); el.setSelectionRange(5, 5); }")
        page.get_by_role("button", name="+ 0.5s", exact=True).click()
        expect(script).to_have_value("Hello [pause=0.5] world")

        # Explicit English-only models do not show a meaningless language selector.
        _choose_dropdown(page, "#model-picker", "Light")
        expect(page.locator("#language-picker")).to_be_hidden(timeout=5_000)
        _choose_dropdown(page, "#model-picker", "Multilingual")
        expect(page.locator("#language-picker")).to_be_visible(timeout=5_000)

        # A clean CI cache has no installed model. Generate must ask before a large
        # download rather than silently fetching files or sending users to a terminal.
        _button(page, "#generate-btn").click()
        expect(page.get_by_role("heading", name="Download Multilingual?", exact=True)).to_be_visible(timeout=10_000)
        expect(page.get_by_text("Nothing downloads until you approve it.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Download & generate", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_visible()
        page.get_by_role("button", name="Cancel", exact=True).click()

        # Compare intentionally lives in a collapsed drawer so it does not dominate the
        # normal Create screen. Open it before validating the explicit opt-in choices.
        compare_panel = page.locator("#compare-panel")
        picker = page.locator("#compare-model-picker")
        if not picker.is_visible():
            compare_panel.locator("button").first.click()
        expect(picker).to_be_visible(timeout=5_000)
        labels = " ".join(picker.locator("label").all_inner_texts())
        assert "Multilingual" in labels
        assert "Expressive" in labels
        assert "Light" in labels
        assert "Install first" in labels
        expect(_button(page, "#compare-btn")).to_be_disabled()

        # Missing models may be selected deliberately so the UI can explain what must
        # be installed, but Compare must remain disabled and never download implicitly.
        multilingual = _check_choice(picker, "Multilingual")
        light = _check_choice(picker, "Light")
        expect(multilingual).to_be_checked()
        expect(light).to_be_checked()
        expect(page.locator("#compare-status")).to_contain_text("Install", timeout=10_000)
        expect(_button(page, "#compare-btn")).to_be_disabled()
        _shot(page, "02-compare-selected.png")

        # Models are a small library: searchable and card-owned actions instead of a
        # detached technical selector.
        page.get_by_role("tab", name="Models", exact=True).click()
        expect(page.get_by_label("Search models", exact=True)).to_be_visible()
        expect(page.get_by_text("Install only what you want", exact=False)).to_be_visible()
        expect(page.get_by_text("23 languages", exact=False)).to_be_visible()
        expect(page.get_by_text("Fast expressive English", exact=False)).to_be_visible()
        expect(page.get_by_text("CPU-only computers", exact=False)).to_be_visible()
        assert page.get_by_role("button", name="Download", exact=True).count() >= 1
        _shot(page, "03-models.png")

        # Speech-to-text setup is available exactly where Transcribe lives.
        page.get_by_role("tab", name="Tools", exact=True).click()
        page.get_by_role("tab", name="Transcribe", exact=True).click()
        expect(_button(page, "#transcribe-btn")).to_be_visible()
        expect(_button(page, "#install-stt-btn")).to_be_visible()
        _shot(page, "04-transcribe.png")

        browser.close()
