"""End-to-end check of the web app in a real Chrome, using the offline fake model (no API key).

    .venv/Scripts/python tests/e2e/run_e2e.py [output_dir]

Starts `reflex run` with a temporary data folder, builds a fake-summarised sample library,
drives the page like a visitor, and writes screenshots to output_dir. Needs Google Chrome
installed and the dev extras (playwright). Not collected by pytest: it is slow and starts servers.
"""

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402
from docx import Document  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from tests.conftest import make_noisy_png  # noqa: E402
from tests.test_pdf import BODY as PDF_BODY, make_pdf  # noqa: E402

FRONTEND, BACKEND = "http://localhost:3000", "http://localhost:8000"
KEY = "sk-ant-e2e-secret-key-do-not-store-123"
results: list[tuple[str, bool, str]] = []


def wait_for(url: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        with contextlib.suppress(Exception):
            if urllib.request.urlopen(url, timeout=2).status < 500:
                return True
        time.sleep(1.5)
    return False


def kill_tree(process: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            with contextlib.suppress(Exception):
                child.kill()
        parent.kill()


def make_user_docx(path: Path) -> None:
    doc = Document()
    doc.add_heading("Pelican Care Guide", level=0)
    doc.add_heading("Feeding", level=1)
    doc.add_paragraph("Pelicans eat about one kilogram of fish every day. Offer fresh herring in the morning.")
    doc.add_heading("Habitat", level=1)
    doc.add_paragraph("A pelican needs a shallow pool at least two meters across and a sunny resting platform.")
    table = doc.add_table(rows=2, cols=2, style="Table Grid")
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Item", "Size"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "Pool", "2 m"
    doc.add_picture(io.BytesIO(make_noisy_png(120, 120)))
    doc.save(path)


def make_long_docx(path: Path, sections: int = 30) -> None:
    """Many real sections, so indexing it takes a while with the slowed-down fake model."""
    doc = Document()
    for i in range(sections):
        doc.add_heading(f"Chapter {i}", level=1)
        doc.add_paragraph(f"Chapter {i} explains one topic in detail. " + "It has plenty to say. " * 40)
    doc.save(path)


def make_plain_docx(path: Path) -> None:
    """A Word file with no heading styles at all: indexing must still give it a navigable outline."""
    doc = Document()
    for i in range(40):
        doc.add_paragraph(f"Heron note {i}. Grey herons wade in shallow water and wait for fish. " + "They stand still. " * 12)
    doc.save(path)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="e2e-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="toc-rag-e2e-"))
    data, samples = work / "data", work / "samples"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "TOC_RAG_LLM_FACTORY": "tests.fake_llm:make_model",
        "TOC_RAG_DATA_DIR": str(data),
        "TOC_RAG_SAMPLES_DIR": str(samples),
        # The sample build runs without the UI, so it needs model names; the offline model ignores them.
        "TOC_RAG_INDEX_MODEL": "offline-index",
        "TOC_RAG_AGENT_MODEL": "offline-agent",
    }
    env.pop("ANTHROPIC_API_KEY", None)

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_samples.py"), "--index", "--out", str(samples)],
        env=env, cwd=ROOT, check=True, capture_output=True,
    )
    log_path = out / "server.log"
    # Each indexing call takes a moment in the app, so "End session" can be pressed while indexing runs.
    server_env = {**env, "TOC_RAG_FAKE_INDEX_DELAY": "0.5"}
    with log_path.open("wb") as log:
        server = subprocess.Popen([sys.executable, "-m", "reflex", "run"], cwd=ROOT, env=server_env, stdout=log, stderr=subprocess.STDOUT)
    try:
        if not (wait_for(f"{BACKEND}/ping", 240) and wait_for(FRONTEND, 240)):
            print("Server did not start. See", log_path)
            return 2
        run_browser(out, data, work)
    finally:
        kill_tree(server)
        shutil.rmtree(work, ignore_errors=True)

    # Reflex reports exceptions in its own frontend code to the server terminal, not the browser console.
    server_log = log_path.read_bytes().decode("utf-8", "replace")
    frontend_errors = [line.strip() for line in server_log.splitlines() if "Frontend Exception" in line or "Error:" in line]
    results.append(("no frontend exceptions in the server log", not frontend_errors, "; ".join(frontend_errors[:3])[:300]))

    print()
    for name, ok, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed. Screenshots and server log: {out}")
    return 1 if failed else 0


def run_browser(out: Path, data: Path, work: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        # Warm-up: the first load of a fresh dev server makes Vite optimize dependencies and hot-swap
        # modules, which can log framework warnings that are not about the app. Load once, let it settle.
        warmup = browser.new_page()
        warmup.goto(FRONTEND, wait_until="networkidle", timeout=120000)
        warmup.wait_for_timeout(5000)
        warmup.close()

        page = browser.new_page(viewport={"width": 1280, "height": 860})
        problems: list[str] = []
        current = ["page load"]
        page.on("pageerror", lambda e: problems.append(f"[{current[0]}] pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"[{current[0]}] console: {m.text}") if m.type == "error" else None)

        def step(name: str, fn, shot: str | None = None) -> None:
            current[0] = name
            try:
                fn()
                results.append((name, True, ""))
            except Exception as exc:
                results.append((name, False, str(exc).splitlines()[0][:200]))
                page.screenshot(path=str(out / f"FAIL-{name[:30].replace(' ', '_')}.png"))
            if shot:
                page.screenshot(path=str(out / shot))

        def dismiss_dialog() -> None:
            """Close the source panel (a section opened from the outline sits on top of it)."""
            close = page.locator("#source-panel button[title='Close panel']")
            seen = []
            for _ in range(4):
                if close.count() == 0:
                    return
                seen.append(f"{close.count()}:{page.locator('#source-panel').inner_text()[:15]!r}")
                close.first.click()
                page.wait_for_timeout(1500)
            panel_text = page.locator("#source-panel").inner_text()[:200]
            assert close.count() == 0, "panel still open: " + repr(panel_text) + " " + " ".join(seen) + " " + "; ".join(problems[-3:])

        page.goto(FRONTEND, wait_until="networkidle", timeout=90000)

        def loads() -> None:
            expect(page.get_by_text("TOC RAG").first).to_be_visible(timeout=30000)
            expect(page.get_by_text("sample", exact=True)).to_have_count(3, timeout=15000)
            expect(page.get_by_placeholder("Add your API key to chat")).to_be_disabled()
            expect(page.get_by_text("Uploads are kept for 2 hours")).to_be_visible()  # no session yet

        step("page loads with 3 sample documents and chat locked", loads, "01-loaded.png")

        def sample_question_without_key() -> None:
            page.get_by_role("button", name="Who is the commander").click()
            expect(page.get_by_text("Add your API key first, then try a sample question.")).to_be_visible(timeout=10000)

        step("a sample question without a key says what is missing", sample_question_without_key)

        def tab_icon() -> None:
            hrefs = page.eval_on_selector_all("link[rel='icon']", "links => links.map(l => l.getAttribute('href'))")
            assert "/favicon.svg" in hrefs, f"no SVG tab icon linked: {hrefs}"
            for href in ("/favicon.svg", "/favicon.ico"):
                response = page.request.get(FRONTEND + href)
                assert response.ok, f"{href} answered {response.status}"

        step("the tab icon is linked and served", tab_icon)

        def browse_sample() -> None:
            page.get_by_role("button", name="Browse").nth(1).click()  # samples are sorted by title: the second is Meridian
            outline = page.locator("#source-panel")
            expect(outline.get_by_text("Outline", exact=True)).to_be_visible(timeout=10000)
            # Roster and Shift schedule are tiny, so indexing merged them into "Crew and Roles".
            outline.get_by_role("button").filter(has=page.get_by_text("Crew and Roles", exact=True)).click()
            expect(page.get_by_text("Ada Okonkwo").first).to_be_visible(timeout=10000)

        step("browse a sample outline and read a section without a key", browse_sample, "02-browse.png")
        step("dialogs close", dismiss_dialog)

        def free_without_key() -> None:
            plain = work / "Heron Notes.docx"
            make_plain_docx(plain)
            page.set_input_files("input[type=file]", str(plain))
            expect(page.locator("[data-preset=full]")).to_be_visible(timeout=30000)
            # The default (Full) needs a key, so indexing is blocked until Free is chosen.
            expect(page.get_by_role("button", name="Index it")).to_be_disabled()
            page.locator("[data-preset=free]").click()
            expect(page.locator("#pending-detail")).to_contain_text("No model calls", timeout=10000)
            expect(page.get_by_role("button", name="Index it")).to_be_enabled()
            page.get_by_role("button", name="Index it").click()
            expect(page.get_by_role("alertdialog")).to_have_count(0)  # Free costs nothing: no confirmation
            expect(page.get_by_role("button", name="Index it")).to_have_count(0, timeout=60000)
            expect(page.get_by_text("Heron Notes").first).to_be_visible(timeout=15000)

        step("the Free preset indexes a heading-less docx without a key", free_without_key, "02b-free.png")

        def session_countdown() -> None:
            # Before the first upload there was no session; now the header counts down to its end, in the browser.
            countdown = page.locator("#session-countdown")
            expect(countdown).to_have_text("2 hours", timeout=1500)  # shown at once; whole hours while an hour or more is left
            # Other times left, by moving the end time on the page (the timer updates as soon as it changes).
            real_end = countdown.get_attribute("data-expires-at")
            for seconds_left, shown in ((3600 + 20, "2 hours"), (59 * 60 + 30, "59 min"), (3 * 60 + 5, "3 min"), (30, "under a minute"), (-5, "now (refresh the page)")):
                page.evaluate(
                    "([s]) => document.getElementById('session-countdown').setAttribute('data-expires-at', String(Date.now() / 1000 + s))",
                    [seconds_left],
                )
                expect(countdown).to_have_text(shown, timeout=2000)
            page.evaluate("([e]) => document.getElementById('session-countdown').setAttribute('data-expires-at', e)", [real_end])
            expect(countdown).to_have_text("2 hours", timeout=2000)

        step("the header counts down to the end of the session", session_countdown)

        def browse_free() -> None:
            page.get_by_role("button", name="Browse").last.click()  # samples come first; Heron Notes is the only upload
            outline = page.locator("#source-panel")
            expect(outline.get_by_text("Heron Notes: Heron note 0.").first).to_be_visible(timeout=10000)
            # A flat outline says why: the document has no headings.
            expect(page.locator("#browse-structure")).to_have_text(
                "No headings found in this document, so its text is split into parts by size.", timeout=10000
            )

        step("its outline shows parts titled by their first sentence", browse_free, "02c-free-outline.png")
        step("the outline closes", dismiss_dialog)

        def pdf_with_bookmarks() -> None:
            brochure = work / "Solar Brochure.pdf"
            make_pdf(brochure, [
                [("p", "Solar Brochure. " + PDF_BODY)],
                [("h", "Choosing panels"), ("p", PDF_BODY * 12)],
                [("h", "Looking after them"), ("p", PDF_BODY * 12)],
            ])
            page.set_input_files("input[type=file]", str(brochure))
            expect(page.locator("[data-preset=free]")).to_be_visible(timeout=30000)
            page.locator("[data-preset=free]").click()
            expect(page.locator("#pending-detail")).to_contain_text("No model calls", timeout=10000)
            page.get_by_role("button", name="Index it").click()
            expect(page.get_by_role("button", name="Index it")).to_have_count(0, timeout=60000)
            row = page.get_by_text("Solar Brochure", exact=True).locator("xpath=ancestor::div[.//button[@title='Delete this document']][1]")
            expect(row).to_be_visible(timeout=15000)
            row.get_by_role("button", name="Browse").click()
            outline = page.locator("#source-panel")
            expect(page.locator("#browse-structure")).to_have_text("Outline from the PDF's bookmarks.", timeout=10000)
            outline.get_by_role("button").filter(has=page.get_by_text("Looking after them", exact=True)).click()
            expect(outline.get_by_text("Solar Brochure · p. 3")).to_be_visible(timeout=10000)  # cited by page
            # The section as its page: a picture of page 3, rendered on request.
            outline.get_by_role("radio", name="Pages").click()
            pages = page.locator("#source-pages img")
            expect(pages).to_have_count(1, timeout=10000)
            expect(page.locator("#source-pages")).to_contain_text("p. 3")
            page.wait_for_function(
                "() => { const i = document.querySelector('#source-pages img'); return i && i.complete && i.naturalWidth >= 1000; }",
                timeout=20000,
            )
            page.screenshot(path=str(out / "02e-pdf-pages.png"))
            outline.get_by_role("radio", name="Text").click()
            expect(page.locator("#source-pages")).to_have_count(0)

        step("a PDF with bookmarks is indexed, its sections cited by page and shown as pages", pdf_with_bookmarks, "02d-pdf.png")
        step("the PDF's section closes", dismiss_dialog)

        def pdf_refusals() -> None:
            locked = work / "Locked.pdf"
            make_pdf(locked, [[("p", PDF_BODY)]], encrypt="secret")
            page.set_input_files("input[type=file]", str(locked))
            expect(page.get_by_text("This PDF is password-protected")).to_be_visible(timeout=20000)
            # Then remove the brochure again, so the rest of the run sees the library it expects.
            row = page.get_by_text("Solar Brochure", exact=True).locator("xpath=ancestor::div[.//button[@title='Delete this document']][1]")
            row.locator("button[title='Delete this document']").click()
            expect(page.get_by_text("Solar Brochure", exact=True)).to_have_count(0, timeout=15000)

        step("a password-protected PDF is refused with a reason", pdf_refusals)

        def pick(trigger, option: str) -> None:
            """Choose from a Radix dropdown. It ignores a click that lands just after it opens (so the opening
            click cannot select by accident), so wait until the list has settled before choosing."""
            trigger.click()
            page.get_by_role("option", name=option).wait_for(state="visible", timeout=10000)
            page.wait_for_timeout(400)
            page.get_by_role("option", name=option).click()

        def enter_key() -> None:
            # Choose OpenAI: the key box and the "get a key" link follow the provider.
            pick(page.get_by_role("combobox", name="Provider"), "OpenAI")
            expect(page.get_by_placeholder("sk-...", exact=True)).to_be_visible(timeout=10000)
            expect(page.get_by_role("link", name="Get a key from OpenAI")).to_have_attribute("href", "https://platform.openai.com/api-keys")
            # A rejected key gives a clear message that names the provider.
            page.locator("input[name=api_key]").fill("sk-bad-key-123")
            page.get_by_role("button", name="Use", exact=True).click()
            expect(page.get_by_text("OpenAI rejected that API key")).to_be_visible(timeout=20000)
            page.locator("input[name=api_key]").fill(KEY)
            page.get_by_role("button", name="Use", exact=True).click()
            expect(page.get_by_text("OpenAI key set")).to_be_visible(timeout=20000)

        step("an OpenAI key is checked and accepted", enter_key, "03-key.png")

        def choose_models() -> None:
            # Nothing is pre-selected: chat stays locked until the visitor picks a chat model.
            pickers = page.locator(".rt-SelectTrigger").filter(has_text="Choose a model")
            expect(pickers).to_have_count(2, timeout=10000)
            expect(page.get_by_placeholder("Choose a chat model to start")).to_be_disabled()
            expect(page.get_by_text("Choose a model for chat (under your API key)")).to_be_visible()
            # Each open list starts with what the model must support, then lists what this key can use.
            for name, note in (("Model for indexing", "Pick a model that supports structured output and images."),
                               ("Model for chat", "Pick a model that supports tool calling.")):
                page.get_by_role("combobox", name=name).click()
                expect(page.get_by_text(note)).to_be_visible(timeout=10000)
                page.keyboard.press("Escape")
                expect(page.get_by_text(note)).to_have_count(0, timeout=10000)
            pick(page.get_by_role("combobox", name="Model for indexing"), "openai-fake-small")
            pick(page.get_by_role("combobox", name="Model for chat"), "openai-fake-large")
            expect(page.locator(".rt-SelectTrigger").filter(has_text="Choose a model")).to_have_count(0, timeout=10000)
            expect(page.get_by_placeholder("Ask a question about the documents")).to_be_enabled(timeout=10000)

        step("both models must be chosen from the key's list before chatting", choose_models, "03b-models.png")

        def key_not_in_page() -> None:
            assert KEY not in page.content(), "key present in the page HTML"
            assert KEY not in page.inner_text("body"), "key present in page text"

        step("the key never appears in the page", key_not_in_page)

        def ask_text() -> None:
            page.get_by_role("button", name="Who is the commander").click()
            expect(page.get_by_text("Next: open document.")).to_be_visible(timeout=60000)  # the model's note before its first step
            expect(page.get_by_text("Answered by the offline test model")).to_be_visible(timeout=60000)

        step("a question streams its path and answers", ask_text, "04-answer.png")

        def path_and_citation() -> None:
            body = page.inner_text("body")
            assert 'Opened "Meridian' in body or "Opened" in body, "no 'Opened' step shown"
            assert "Read " in body, "no 'Read' step shown"
            assert "Next: " in body, "the model's note before a step is not shown beside it"
        step("navigation steps are shown", path_and_citation)

        def open_citation() -> None:
            # Citation chips carry the document title (the trash buttons of uploads also have a title).
            chips = page.locator("button[title]:not([title='Delete this document'])", has=page.locator("svg"))
            expect(chips.first).to_be_visible(timeout=10000)
            chips.first.click()
            expect(page.locator("#source-panel").get_by_text("Source", exact=True)).to_be_visible(timeout=10000)

        step("a citation opens the source section", open_citation, "05-source.png")
        step("the source dialog closes", dismiss_dialog)

        def ask_image() -> None:
            box = page.get_by_placeholder("Ask a question about the documents")
            box.fill("Show me the oxygen reserve chart")
            box.press("Enter")
            image = page.locator("img[alt*='bar chart']").first
            expect(image).to_be_visible(timeout=60000)
            page.wait_for_function("() => [...document.images].some(i => i.alt.includes('bar chart') && i.naturalWidth > 0)", timeout=15000)

        step("an image question shows the picture inline", ask_image, "06-image.png")

        def enlarge_image() -> None:
            page.locator("[data-zoom-area] img[alt*='bar chart']").first.click()
            overlay = page.locator("#image-zoom")
            expect(overlay).to_be_visible(timeout=5000)
            expect(overlay.locator("img")).to_have_attribute("alt", re.compile("bar chart"))
            expect(overlay.locator(".image-zoom-caption")).to_contain_text("bar chart")
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / "06b-enlarged.png"))
            close = overlay.get_by_role("button", name="Close")
            box, icon = close.bounding_box(), close.locator("svg").bounding_box()
            centre_off = abs((icon["x"] + icon["width"] / 2) - (box["x"] + box["width"] / 2)) + abs(
                (icon["y"] + icon["height"] / 2) - (box["y"] + box["height"] / 2)
            )
            assert centre_off < 1, f"the close icon is {centre_off:.1f}px off centre"
            page.keyboard.press("Escape")
            expect(overlay).to_have_count(0)
            for closing in (lambda: close.click(), lambda: overlay.locator("img").click()):  # the button, or anywhere
                page.locator("[data-zoom-area] img[alt*='bar chart']").first.click()
                expect(overlay).to_be_visible(timeout=5000)
                closing()
                expect(overlay).to_have_count(0)

        step("a picture enlarges on click, and closes", enlarge_image)

        def scrolled_up_stays_up() -> None:
            viewport = "document.getElementById('chat-end').closest('[data-radix-scroll-area-viewport]')"
            answers_before = page.locator("text=Answered by the offline test model").count()
            box = page.get_by_placeholder("Ask a question about the documents")
            box.fill("How often should I clean my solar panels?")
            box.press("Enter")
            expect(page.get_by_text("Working...")).to_be_visible(timeout=15000)
            page.evaluate(f"{viewport}.scrollTop = 0")  # the visitor scrolls up to re-read while it streams
            expect(page.locator("text=Answered by the offline test model")).to_have_count(answers_before + 1, timeout=60000)
            page.wait_for_timeout(800)
            top = page.evaluate(f"{viewport}.scrollTop")
            assert top < 50, f"the chat was scrolled back down to {top}px while the visitor was reading above"

        step("scrolling up while an answer streams is not undone", scrolled_up_stays_up)

        def bad_content() -> None:
            fake = work / "fake.docx"
            fake.write_text("this is not really a word document")
            page.set_input_files("input[type=file]", str(fake))
            expect(page.get_by_text("not a valid Word")).to_be_visible(timeout=20000)

        step("a fake .docx is refused by content", bad_content)

        def drop_zone_is_the_button() -> None:
            # Choosing a file starts the upload by itself, so there is no separate Upload button,
            # and the drop zone shows a pointer like any other clickable thing.
            expect(page.get_by_role("button", name="Upload", exact=True)).to_have_count(0)
            cursor = page.evaluate("getComputedStyle(document.querySelector('.rx-Upload')).cursor")
            assert cursor == "pointer", f"the drop zone's cursor is {cursor!r}"

        step("the drop zone uploads on its own and shows a pointer", drop_zone_is_the_button)

        def upload_real() -> None:
            mine = work / "Pelican Care Guide.docx"
            make_user_docx(mine)
            page.set_input_files("input[type=file]", str(mine))
            expect(page.locator("[data-preset=full]")).to_be_visible(timeout=30000)
            page.locator("[data-preset=budget]").click()
            detail = page.locator("#pending-detail")
            expect(detail).to_contain_text("model calls on your key", timeout=10000)
            budget = detail.inner_text()
            page.locator("[data-preset=full]").click()
            expect(detail).not_to_have_text(budget, timeout=10000)  # Full adds image descriptions and longer summaries
            expect(detail).to_contain_text("1 image to describe")

        step("a real docx shows a cost estimate per preset before indexing", upload_real, "07-estimate.png")

        def clickables_show_a_hand() -> None:
            """Every visible, enabled clickable thing shows the pointer cursor, including the advanced options."""
            page.get_by_role("button", name="Advanced options").click()
            expect(page.get_by_text("Small sections", exact=True)).to_be_visible(timeout=10000)
            offenders = page.evaluate("""() => {
                const selector = 'button, [role=button], [role=radio], [role=switch], [role=combobox], [role=checkbox], .rx-Upload';
                return [...document.querySelectorAll(selector)]
                    .filter(el => el.offsetParent !== null && !el.disabled && el.getAttribute('aria-disabled') !== 'true'
                                  && !el.hasAttribute('data-disabled'))
                    .filter(el => getComputedStyle(el).cursor !== 'pointer')
                    .map(el => `${el.tagName.toLowerCase()}[${el.getAttribute('role') || ''}] "${(el.innerText || el.title || '').trim().slice(0, 30)}": ${getComputedStyle(el).cursor}`);
            }""")
            page.get_by_role("button", name="Advanced options").click()
            assert not offenders, "no hand cursor on: " + "; ".join(offenders[:8])

        step("everything clickable shows a hand cursor", clickables_show_a_hand)

        def agent_settings() -> None:
            # Collapsed at first; opening it shows the listing depth, which can be changed.
            expect(page.locator("#listing-depth")).to_have_count(0)
            page.locator("#agent-settings-toggle").click()
            depth = page.locator("#listing-depth")
            expect(depth).to_contain_text("2", timeout=10000)
            pick(depth, "3")
            expect(depth).to_contain_text("3", timeout=10000)
            pick(depth, "2")
            expect(depth).to_contain_text("2", timeout=10000)

        step("agent settings open and change the listing depth", agent_settings)

        def index_and_confirm() -> None:
            """Index it with a paid preset: a confirmation shows the cost first."""
            page.get_by_role("button", name="Index it").click()
            dialog = page.get_by_role("alertdialog")
            expect(dialog.locator("#confirm-cost")).to_contain_text("Model calls", timeout=10000)
            dialog.get_by_role("button", name="Start indexing").click()
            expect(dialog).to_have_count(0, timeout=10000)

        def index_it() -> None:
            # Cancel in the confirmation: nothing starts, and the document is still waiting.
            page.get_by_role("button", name="Index it").click()
            dialog = page.get_by_role("alertdialog")
            expect(dialog).to_contain_text("Index Pelican Care Guide?", timeout=10000)
            expect(dialog.locator("#confirm-cost")).to_contain_text(" in, ", timeout=10000)
            page.wait_for_timeout(500)  # let the popup finish fading in before the screenshot
            page.screenshot(path=str(out / "07b-confirm.png"))
            dialog.get_by_role("button", name="Cancel").click()
            expect(dialog).to_have_count(0, timeout=10000)
            expect(page.get_by_role("button", name="Index it")).to_be_visible()
            index_and_confirm()
            # The button disappears as soon as indexing starts; the toast means it has finished and saved.
            expect(page.get_by_text("Indexed Pelican Care Guide").first).to_be_visible(timeout=60000)
            expect(page.get_by_role("button", name="Index it")).to_have_count(0, timeout=60000)

        step("indexing completes and the document joins the library", index_it, "08-indexed.png")

        def ask_mine() -> None:
            box = page.get_by_placeholder("Ask a question about the documents")
            box.fill("How much fish does a pelican eat every day?")
            box.press("Enter")
            expect(page.get_by_text("one kilogram of fish").first).to_be_visible(timeout=60000)

        step("the uploaded document can be searched", ask_mine, "09-mine.png")

        pelican = work / "Pelican Care Guide.docx"  # the same file as before, so the same document

        def reupload_and_cancel() -> None:
            page.set_input_files("input[type=file]", str(pelican))
            note = page.locator("#replace-note")
            expect(note).to_contain_text("Already in your library (indexed with Full · OpenAI openai-fake-small)", timeout=30000)
            page.get_by_role("button", name="Cancel").click()
            expect(note).to_have_count(0, timeout=10000)
            expect(page.get_by_text("Pelican Care Guide", exact=True)).to_have_count(1)  # still in the library
            page.get_by_role("button", name="Browse").last.click()  # its index and outline survived the cancel
            expect(page.locator("#source-panel").get_by_text("Pelican Care Guide").first).to_be_visible(timeout=10000)

        step("re-uploading a document offers to replace it; cancel keeps it", reupload_and_cancel, "09b-replace.png")
        step("the outline closes after the re-upload check", dismiss_dialog)

        def reindex_with_budget() -> None:
            page.set_input_files("input[type=file]", str(pelican))
            expect(page.locator("#replace-note")).to_be_visible(timeout=30000)
            page.locator("[data-preset=budget]").click()
            index_and_confirm()
            expect(page.get_by_text("Indexed Pelican Care Guide").first).to_be_visible(timeout=60000)
            expect(page.get_by_text("Pelican Care Guide", exact=True)).to_have_count(1)  # replaced, not added
            page.set_input_files("input[type=file]", str(pelican))
            expect(page.locator("#replace-note")).to_contain_text("indexed with Budget · OpenAI openai-fake-small", timeout=30000)
            page.get_by_role("button", name="Cancel").click()
            expect(page.locator("#replace-note")).to_have_count(0, timeout=10000)

        step("indexing it again replaces the old version", reindex_with_budget)

        def sample_file_is_refused() -> None:
            page.set_input_files("input[type=file]", str(ROOT / "samples" / "source" / "home-solar-installation-guide.docx"))
            expect(page.get_by_text("one of the sample documents")).to_be_visible(timeout=20000)

        step("uploading a sample document is refused", sample_file_is_refused)

        def key_not_on_disk() -> None:
            hits = [str(f) for f in data.rglob("*") if f.is_file() and KEY.encode() in f.read_bytes()]
            assert not hits, f"key found in {hits}"
            assert any((data / "sessions").iterdir()), "no session folder was created"

        step("the key is not written anywhere on disk", key_not_on_disk)

        def delete_mine() -> None:
            delete = page.locator("button[title='Delete this document']")
            expect(delete).to_have_count(2, timeout=15000)  # Heron Notes and Pelican Care Guide
            for remaining in (1, 0):
                delete.first.click()
                expect(delete).to_have_count(remaining, timeout=15000)
            expect(page.get_by_text("sample", exact=True)).to_have_count(3)

        step("deleting your documents removes them", delete_mine)

        def end_session_while_indexing() -> None:
            long_doc = work / "Long Report.docx"
            make_long_docx(long_doc)
            page.set_input_files("input[type=file]", str(long_doc))
            page.locator("[data-preset=full]").click(timeout=30000)
            index_and_confirm()
            expect(page.get_by_text("Indexing with your key...")).to_be_visible(timeout=15000)
            page.wait_for_timeout(1500)  # a few summaries done, most still to go
            page.get_by_role("button", name="End session and delete my data").click()
            expect(page.get_by_text("Indexing was stopped").first).to_be_visible(timeout=15000)
            expect(page.locator("input[name=api_key]")).to_be_visible(timeout=10000)
            expect(page.get_by_text("Indexing with your key...")).to_have_count(0)
            # Give the job time to wind down; it must not save or recreate the deleted folder.
            page.wait_for_timeout(6000)
            leftovers = [d for d in (data / "sessions").iterdir()] if (data / "sessions").exists() else []
            assert not leftovers, f"session folder left behind: {leftovers}"
            expect(page.get_by_text("Long Report")).to_have_count(0)
            expect(page.get_by_text("Indexing failed")).to_have_count(0)

        step("ending the session mid-indexing stops it and deletes everything", end_session_while_indexing, "10-ended.png")

        def mobile() -> None:
            page.set_viewport_size({"width": 375, "height": 800})
            page.wait_for_timeout(600)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"page is {overflow}px wider than the screen"

        step("the phone layout does not scroll sideways", mobile, "11-mobile.png")

        real_problems = [p for p in problems if "favicon" not in p and "Failed to load resource" not in p]
        (out / "browser-errors.txt").write_text("\n\n".join(problems), encoding="utf-8")
        results.append(("no JavaScript errors in the browser", not real_problems, "; ".join(p[:150] for p in real_problems[:3])))
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
