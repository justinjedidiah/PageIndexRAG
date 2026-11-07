"""The home page at "/": what TOC RAG is, how it works, and a way into the app at "/app".

Static: it uses no State, so it renders before the backend is reached and costs nothing per visit.
The wording follows README.md; keep the two in step.
"""

import reflex as rx

from toc_rag.sessions import describe_duration

from .runtime import settings
from .views import BORDER, EXAMPLES, PRIMARY, SURFACE

APP_ROUTE = "/app"
GITHUB_URL = "https://github.com/justinjedidiah/TOC-RAG"
PAGEINDEX_URL = "https://github.com/VectifyAI/PageIndex"
MAX_WIDTH = "1100px"


def _logo() -> rx.Component:
    return rx.center(rx.icon("book-open-text", size=18), width="36px", height="36px", border=BORDER, border_radius="10px", background=SURFACE)


def _open_app_button(label: str = "Open the app", size: str = "2") -> rx.Component:
    return rx.link(
        rx.button(label, rx.icon("arrow-right", size=16), size=size, radius="full", **PRIMARY),
        href=APP_ROUTE,
        underline="none",
    )


def _section(*children, id: str | None = None, **props) -> rx.Component:
    """A full-width band of the page with its content centred and capped in width."""
    return rx.box(
        rx.box(*children, max_width=MAX_WIDTH, margin="0 auto", width="100%"),
        id=id,
        padding_x="16px",
        padding_y=rx.breakpoints(initial="3em", md="4.5em"),
        width="100%",
        **props,
    )


def _heading(eyebrow: str, title: str, intro: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(eyebrow, size="2", weight="medium", color_scheme="gray"),
        rx.heading(title, size=rx.breakpoints(initial="6", md="7"), weight="bold"),
        *([rx.text(intro, size="3", color_scheme="gray", max_width="680px")] if intro else []),
        spacing="2",
        align="start",
        margin_bottom="2em",
    )


def nav_bar() -> rx.Component:
    return rx.box(
        rx.hstack(
            _logo(),
            rx.text("TOC RAG", size="3", weight="bold"),
            rx.spacer(),
            rx.link(
                rx.hstack(rx.icon("code", size=16), rx.text("GitHub", size="2", display=["none", "block"]), spacing="1", align="center"),
                href=GITHUB_URL,
                is_external=True,
                color_scheme="gray",
                high_contrast=True,
                underline="none",
            ),
            _open_app_button(size="2"),
            spacing="4",
            align="center",
            max_width=MAX_WIDTH,
            margin="0 auto",
            width="100%",
        ),
        padding="0.75em 16px",
        border_bottom=BORDER,
        background=SURFACE,
        position="sticky",
        top="0",
        z_index="10",
    )


# ---- hero ----


def _demo_step(label: str) -> rx.Component:
    """One navigation step, drawn like `step_line` in the app."""
    return rx.hstack(
        rx.icon("corner-down-right", size=12, color="var(--gray-9)"),
        rx.text(label, size="1", color_scheme="gray"),
        spacing="1",
        align="center",
    )


def demo_answer() -> rx.Component:
    """A real question on the sample library, answered the way the app shows it. Static, for illustration."""
    return rx.box(
        rx.vstack(
            rx.box(
                rx.text(EXAMPLES[0], size="2"),
                align_self="flex-end",
                background="var(--gray-4)",
                padding="0.55em 0.95em",
                border_radius="16px",
                max_width="85%",
            ),
            rx.vstack(
                _demo_step("Listed documents"),
                _demo_step('Opened "Meridian Station operations handbook"'),
                _demo_step('Read "Crew and Roles"'),
                spacing="1",
                align="start",
            ),
            rx.text(
                "The commander is Ada Okonkwo, who works the day shift: 06:00 to 14:00 station time.",
                size="2",
            ),
            rx.badge(
                rx.icon("file-text", size=12),
                "Crew and Roles",
                rx.text("Roster, Shift schedule", color_scheme="gray"),
                variant="outline",
                color_scheme="gray",
                radius="full",
                size="1",
            ),
            spacing="3",
            align="start",
            width="100%",
        ),
        padding="1.25em",
        border=BORDER,
        border_radius="18px",
        background=SURFACE,
        box_shadow="0 12px 40px -18px rgba(0, 0, 0, 0.25)",
        width="100%",
    )


def hero() -> rx.Component:
    return _section(
        rx.grid(
            rx.vstack(
                rx.badge("Vectorless RAG", variant="surface", color_scheme="gray", radius="full", size="2"),
                rx.heading("Retrieval without vectors", size=rx.breakpoints(initial="8", md="9"), weight="bold", line_height="1.05"),
                rx.text(
                    "An AI agent answers questions about your documents by navigating their table of contents, "
                    "the way a person would: open the document, scan the outline, step into the right section, "
                    "read it, and cite it. No chunking, no embeddings, no similarity search.",
                    size="4",
                    color_scheme="gray",
                ),
                rx.hstack(
                    _open_app_button("Try it now", size="3"),
                    rx.el.a(
                        rx.button("How it works", size="3", radius="full", variant="outline", color_scheme="gray"),
                        href="#how-it-works",
                    ),
                    spacing="3",
                    wrap="wrap",
                ),
                rx.text(
                    "Bring your own Anthropic, OpenAI or Google Gemini key. The sample library can be browsed without one.",
                    size="2",
                    color_scheme="gray",
                ),
                spacing="5",
                align="start",
            ),
            demo_answer(),
            columns=rx.breakpoints(initial="1", md="2"),
            spacing="7",
            align="center",
            width="100%",
        ),
    )


# ---- how it works ----

STEPS = [
    (
        "list-tree",
        "Index once",
        "Each document becomes a tree of sections built from its own headings, bookmarks or printed contents page. "
        "Sections too big to read in one go are split; runs of tiny ones are merged. A good outline is kept as it is.",
    ),
    (
        "notebook-pen",
        "Summarise",
        "A small model writes a short summary of every section, saying what questions it can answer, and describes "
        "the images. Or skip it: the free preset shows each section's first sentences instead.",
    ),
    (
        "route",
        "Navigate",
        "For each question the agent gets a few tools (open a document, list a section's subsections, read a "
        "section, go back up) and walks the tree, choosing branches by their summaries.",
    ),
    (
        "quote",
        "Cite",
        "The answer cites sections as it goes. A citation is only kept if the agent actually read that section, "
        "so the app never shows a source the model only guessed at.",
    ),
]


def _step_card(number: int, icon: str, title: str, text: str) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.center(rx.icon(icon, size=18), width="36px", height="36px", border_radius="10px", background="var(--gray-3)"),
            rx.text(f"{number}", size="1", weight="bold", color_scheme="gray"),
            justify="between",
            align="center",
            width="100%",
        ),
        rx.text(title, size="4", weight="bold"),
        rx.text(text, size="2", color_scheme="gray"),
        spacing="3",
        align="start",
        padding="1.25em",
        border=BORDER,
        border_radius="14px",
        background=SURFACE,
        height="100%",
    )


def how_it_works() -> rx.Component:
    return _section(
        _heading("How it works", "Read documents like a person does", "Four steps, from upload to a cited answer."),
        rx.grid(
            *[_step_card(i + 1, icon, title, text) for i, (icon, title, text) in enumerate(STEPS)],
            columns=rx.breakpoints(initial="1", sm="2", lg="4"),
            spacing="4",
            width="100%",
        ),
        id="how-it-works",
        background="var(--gray-2)",
        border_top=BORDER,
        border_bottom=BORDER,
    )


# ---- comparison ----

COMPARISON = [
    ("Finds text by", "similarity of embedded chunks", "following the document's own structure"),
    ("Context the model sees", "isolated chunks", "whole sections, in their place in the outline"),
    ("Explains its choice", "no (a similarity score)", "yes: every step of the path is shown"),
    ("Needs", "an embedding model and a vector store", "nothing beyond the chat model"),
]


def comparison() -> rx.Component:
    return _section(
        _heading("Why not vector RAG?", "Structure instead of similarity"),
        rx.box(
            rx.table.root(
                rx.table.header(
                    rx.table.row(
                        rx.table.column_header_cell(""),
                        rx.table.column_header_cell("Vector RAG"),
                        rx.table.column_header_cell("TOC RAG"),
                    )
                ),
                rx.table.body(
                    *[
                        rx.table.row(
                            rx.table.row_header_cell(rx.text(label, weight="medium")),
                            rx.table.cell(rx.text(vector, color_scheme="gray")),
                            rx.table.cell(ours),
                        )
                        for label, vector, ours in COMPARISON
                    ]
                ),
                variant="surface",
                size="2",
                width="100%",
            ),
            overflow_x="auto",
            width="100%",
        ),
        rx.text(
            "The trade-off: navigation takes a few model calls per question, and it depends on documents having "
            "(or being given) a sensible structure.",
            size="2",
            color_scheme="gray",
            margin_top="1em",
        ),
    )


# ---- features ----


def features() -> rx.Component:
    lifetime = describe_duration(settings.session_ttl_seconds)
    items = [
        ("key-round", "Bring your own key", "Anthropic, OpenAI or Google Gemini. Pick the indexing and chat models from the list your key can use."),
        ("file-text", "PDF and Word", "With or without a table of contents: bookmarks, a printed contents page, headings, or headings found by their look."),
        ("calculator", "Cost before you spend", "Free, Budget and Full indexing presets, each with an estimate. The free one makes no model calls at all."),
        ("radio", "Live answers", "The agent's path streams as it works, then the answer, with clickable citations and images shown inline."),
        ("image", "Charts and tables", "Charts and pictures become images a vision model describes; ruled tables are kept as tables."),
        ("library", "Sample library", "Three documents to try without uploading anything."),
    ]
    return _section(
        _heading("Features", "What you get"),
        rx.grid(
            *[
                rx.hstack(
                    rx.center(rx.icon(icon, size=18), min_width="36px", height="36px", border_radius="10px", background="var(--gray-3)"),
                    rx.vstack(rx.text(title, size="3", weight="bold"), rx.text(text, size="2", color_scheme="gray"), spacing="1", align="start"),
                    spacing="3",
                    align="start",
                )
                for icon, title, text in items
            ],
            columns=rx.breakpoints(initial="1", sm="2", md="3"),
            spacing="6",
            width="100%",
        ),
        rx.callout(
            f"Your key stays in server memory for your session only: never written to disk or logs. "
            f"Everything you upload is deleted when you end the session, or after {lifetime}.",
            icon="shield-check",
            color_scheme="gray",
            size="2",
            margin_top="2.5em",
        ),
        background="var(--gray-2)",
        border_top=BORDER,
        border_bottom=BORDER,
    )


# ---- end ----


def closing() -> rx.Component:
    return _section(
        rx.vstack(
            rx.heading("Ask your documents a question", size=rx.breakpoints(initial="6", md="7"), weight="bold", text_align="center"),
            rx.text("Start with the sample library, then upload your own PDF or Word file.", size="3", color_scheme="gray", text_align="center"),
            _open_app_button("Open the app", size="3"),
            spacing="4",
            align="center",
        ),
    )


def footer() -> rx.Component:
    return rx.box(
        rx.flex(
            rx.text(
                "Inspired by ",
                rx.link("PageIndex", href=PAGEINDEX_URL, is_external=True, color_scheme="gray", high_contrast=True),
                " by VectifyAI. An independent project, not affiliated with VectifyAI. "
                "Built with Reflex, LangGraph and LangChain.",
                size="1",
                color_scheme="gray",
            ),
            rx.link("Source on GitHub", href=GITHUB_URL, is_external=True, size="1", color_scheme="gray", high_contrast=True),
            direction=rx.breakpoints(initial="column", sm="row"),
            justify="between",
            gap="2",
            max_width=MAX_WIDTH,
            margin="0 auto",
            width="100%",
        ),
        padding="1.5em 16px",
        border_top=BORDER,
        background=SURFACE,
    )


def home() -> rx.Component:
    return rx.box(
        nav_bar(),
        hero(),
        how_it_works(),
        comparison(),
        features(),
        closing(),
        footer(),
        background=SURFACE,
        min_height="100vh",
        overflow_x="hidden",
    )
