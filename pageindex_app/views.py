"""The page, built from small components."""

import reflex as rx

from pageindex_rag.providers import PROVIDERS

from .runtime import settings
from .state import MAX_LEAF_CHOICES, MIN_LEAF_CHOICES, UPLOAD_ID, State
from .viewmodels import ChatMessage, CitationView, DocView, ImageView, OutlineRowView, PresetView, StepView

EXAMPLES = [
    "Who is the commander of Meridian Station, and what shift do they work?",
    "What was Nimbus Robotics' total revenue in Q3 2026?",
    "Show me the oxygen reserve chart from the station handbook.",
    "How often should I clean my solar panels?",
]
ACCEPTED = {
    "application/pdf": [".pdf"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
}
MUTED = {"size": "1", "color_scheme": "gray"}
BORDER = "1px solid var(--gray-5)"
SURFACE = "var(--color-panel-solid)"
PRIMARY = {"high_contrast": True}
LABEL = {"size": "2", "color_scheme": "gray", "weight": "medium"}
CARD = {"background": SURFACE, "border": BORDER, "border_radius": "14px", "box_shadow": "none", "width": "100%", "flex_shrink": "0"}
PROSE_STYLE = {
    "& table": {"border_collapse": "collapse", "margin": "0.5em 0"},
    "& th, & td": {"padding": "4px 12px", "border": "1px solid var(--gray-6)", "text_align": "left"},
    "& th": {"background": "var(--gray-3)"},
    "& img": {"max_width": "100%", "border_radius": "8px"},
}


@rx.memo
def prose(text: rx.Var[str]) -> rx.Component:
    """Markdown with readable tables and images that fit.

    A memo component, not a plain function: Reflex's markdown element calls a hook while it is
    built, and that is only valid inside a component of its own, never in a list or a condition.
    """
    return rx.box(rx.markdown(text), style=PROSE_STYLE)


# ---- sidebar ----


def model_select(label: str, value: rx.Var, on_change, hint: str, note: str) -> rx.Component:
    """A dropdown of the models this key can use. `note` sits at the top of the open list: what the model must support."""
    return rx.vstack(
        rx.text(label, size="1", weight="medium"),
        rx.select.root(
            rx.select.trigger(placeholder="Choose a model", width="100%", custom_attrs={"aria-label": label}),
            rx.select.content(
                rx.select.group(
                    rx.select.label(note),
                    rx.foreach(State.models, lambda model: rx.select.item(model, value=model)),
                ),
            ),
            value=value,
            on_change=on_change,
            size="1",
        ),
        rx.text(hint, **MUTED),
        spacing="1",
        width="100%",
    )


def key_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text("1. Your API key", size="2", weight="bold"),
            rx.cond(
                State.key_set,
                rx.vstack(
                    rx.hstack(
                        rx.badge(f"{State.provider_label} key set", color_scheme="green"),
                        rx.button("Remove", variant="ghost", size="1", on_click=State.clear_key),
                        align="center",
                    ),
                    model_select(
                        "Model for indexing", State.index_model, State.set_index_model,
                        "Writes summaries and describes images. A small, cheap model is enough.",
                        "Pick a model that supports structured output and images.",
                    ),
                    model_select(
                        "Model for chat", State.agent_model, State.set_agent_model,
                        "Navigates the documents and answers. Must support tool calling.",
                        "Pick a model that supports tool calling.",
                    ),
                    spacing="3",
                    width="100%",
                ),
                rx.vstack(
                    # Outside the form on purpose: the form clears itself after each submit (so the key does
                    # not linger in the box), and a select inside it would snap back to its first value.
                    rx.select.root(
                        rx.select.trigger(width="100%", custom_attrs={"aria-label": "Provider"}),
                        rx.select.content(
                            *[rx.select.item(info.label, value=pid) for pid, info in PROVIDERS.items()]
                        ),
                        value=State.provider,
                        on_change=State.set_provider,
                        size="2",
                    ),
                    rx.form(
                        rx.hstack(
                            rx.input(
                                name="api_key",
                                type="password",
                                placeholder=State.key_placeholder,
                                auto_complete="off",
                                width="100%",
                            ),
                            rx.button("Use", type="submit", loading=State.key_checking, **PRIMARY),
                            width="100%",
                        ),
                        on_submit=State.save_key,
                        reset_on_submit=True,
                        width="100%",
                    ),
                    rx.link(
                        f"Get a key from {State.provider_label}",
                        href=State.key_url,
                        is_external=True,
                        size="1",
                    ),
                    spacing="2",
                    width="100%",
                ),
            ),
            rx.cond(State.key_error != "", rx.text(State.key_error, size="2", color_scheme="red")),
            rx.text(
                f"Anthropic, OpenAI or Google Gemini. Needed to chat, and to index with AI summaries. It stays in "
                f"this session's server memory only: never saved to disk or logged, and sent only to {State.provider_label}.",
                **MUTED,
            ),
            spacing="2",
            align="start",
            width="100%",
        ),
        **CARD,
    )


def doc_row(doc: DocView) -> rx.Component:
    return rx.hstack(
        rx.vstack(
            # One line: a long name ends in "..." and shows in full on hover.
            rx.text(
                doc.title,
                size="2",
                weight="medium",
                white_space="nowrap",
                overflow="hidden",
                text_overflow="ellipsis",
                max_width="100%",
                title=doc.title,
            ),
            # Plain text, not a badge, so "sample" lines up with the file type.
            rx.hstack(
                rx.cond(doc.sample, rx.text("sample", size="1", weight="medium", color_scheme="amber")),
                rx.cond(doc.sample, rx.text("·", **MUTED)),
                rx.text(doc.kind, **MUTED),
                spacing="1",
                align="center",
            ),
            spacing="0",
            align="start",
            flex="1",
            min_width="0",  # lets the title shrink and truncate instead of pushing the buttons out
        ),
        rx.button("Browse", size="1", variant="soft", color_scheme="gray", on_click=State.open_browse(doc.doc_id)),
        rx.cond(
            ~doc.sample,
            rx.icon_button(
                rx.icon("trash-2", size=14),
                size="1",
                variant="ghost",
                color_scheme="red",
                title="Delete this document",
                on_click=State.delete_doc(doc.doc_id),
            ),
        ),
        width="100%",
        align="center",
        spacing="2",
        padding_y="0.4em",
        border_top=BORDER,
    )


def library_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text("Library", size="2", weight="bold"),
            rx.cond(
                State.docs.length() > 0,
                rx.vstack(rx.foreach(State.docs, doc_row), spacing="0", width="100%"),
                rx.text("No documents yet.", **MUTED),
            ),
            rx.text(State.session_note, **MUTED),
            spacing="2",
            align="start",
            width="100%",
        ),
        **CARD,
    )


def upload_form() -> rx.Component:
    """A drop zone that starts reading the file as soon as one is chosen or dropped: no separate Upload button."""
    return rx.vstack(
        rx.upload(
            rx.cond(
                State.building,
                rx.vstack(
                    rx.spinner(size="2"),
                    rx.text("Reading the document...", size="2", text_align="center"),
                    align="center",
                    spacing="2",
                ),
                rx.vstack(
                    rx.icon("upload", size=22),
                    rx.text("Drop a .docx or .pdf here, or click to choose", size="2", text_align="center"),
                    rx.text(f"Up to {settings.max_upload_bytes // (1024 * 1024)} MB. ZIP files are not accepted.", **MUTED),
                    align="center",
                    spacing="1",
                ),
            ),
            id=UPLOAD_ID,
            accept=ACCEPTED,
            max_files=1,
            max_size=settings.max_upload_bytes,
            multiple=False,
            on_drop=State.handle_upload,
            disabled=State.building,
            padding="1em",
            border="1px dashed var(--gray-7)",
            border_radius="12px",
            background="var(--gray-2)",
            width="100%",
            cursor=rx.cond(State.building, "progress", "pointer"),
            _hover={"border_color": "var(--gray-9)", "background": "var(--gray-3)"},
        ),
        rx.text("You choose how to index it (and see the cost) before anything is spent.", **MUTED),
        spacing="2",
        width="100%",
    )


def preset_option(choice: PresetView) -> rx.Component:
    chosen = State.preset == choice.name
    return rx.box(
        rx.hstack(
            rx.text(choice.label, size="2", weight="bold"),
            rx.text(choice.description, size="1", color_scheme="gray"),
            align="baseline",
            wrap="wrap",
            spacing="2",
        ),
        rx.text(choice.cost, size="1"),
        on_click=State.set_preset(choice.name),
        cursor="pointer",
        width="100%",
        padding="0.5em 0.7em",
        border_radius="10px",
        # The border width never changes (only its colour); the extra ring is a shadow, which takes no space.
        # A 1px -> 2px border would resize the cards and shift the whole panel on every selection.
        border=rx.cond(chosen, "1px solid var(--accent-9)", BORDER),
        box_shadow=rx.cond(chosen, "0 0 0 1px var(--accent-9)", "none"),
        background=rx.cond(chosen, "var(--accent-2)", "transparent"),
        custom_attrs={"role": "radio", "aria-checked": rx.cond(chosen, "true", "false"), "data-preset": choice.name},
    )


def option_row(label: str, control: rx.Component) -> rx.Component:
    return rx.hstack(rx.text(label, size="1", flex="1"), control, align="center", width="100%")


def choice_select(items: list[tuple[str, str]], value: rx.Var, on_change, **props) -> rx.Component:
    """A small select whose items show a label but send a value."""
    return rx.select.root(
        rx.select.trigger(min_width="9em"),
        rx.select.content(*[rx.select.item(label, value=item) for item, label in items]),
        value=value,
        on_change=on_change,
        size="1",
        **props,
    )


def advanced_options() -> rx.Component:
    return rx.vstack(
        option_row(
            "Split sections too big to read in one go",
            rx.switch(checked=State.opt_split, on_change=State.set_opt_split, size="1"),
        ),
        option_row(
            "Largest section before splitting",
            choice_select([(c, f"{c} tokens") for c in MAX_LEAF_CHOICES], State.opt_max_leaf, State.set_opt_max_leaf),
        ),
        option_row(
            "Small sections",
            choice_select(
                [("off", "Keep as they are"), ("merge", "Merge tiny ones"), ("fold", "Merge and fold")],
                State.opt_small,
                State.set_opt_small,
            ),
        ),
        option_row(
            "Tiny means under",
            choice_select([(c, f"{c} tokens") for c in MIN_LEAF_CHOICES], State.opt_min_leaf, State.set_opt_min_leaf),
        ),
        option_row(
            "Section summaries",
            choice_select(
                [("llm", "AI summaries"), ("preview", "Free previews")], State.opt_summaries, State.set_opt_summaries
            ),
        ),
        option_row(
            "Summary length",
            choice_select(
                [("short", "Short"), ("detailed", "Detailed")],
                State.opt_summary_length,
                State.set_opt_summary_length,
                disabled=State.opt_summaries == "preview",
            ),
        ),
        option_row(
            "Describe images with the model",
            rx.switch(checked=State.opt_images, on_change=State.set_opt_images, size="1"),
        ),
        rx.text(
            "Merging joins runs of tiny sibling sections and sections with a single subsection. Folding also turns "
            "any part of the outline small enough to read in one go into one section. Original headings are kept.",
            **MUTED,
        ),
        spacing="2",
        width="100%",
        padding="0.6em",
        border=BORDER,
        border_radius="10px",
    )


def pending_view() -> rx.Component:
    return rx.vstack(
        rx.text(State.pending_name, weight="medium", size="2", word_break="break-word"),
        rx.vstack(
            rx.foreach(State.preset_choices, preset_option),
            spacing="2",
            width="100%",
            custom_attrs={"role": "radiogroup"},
        ),
        rx.button(
            rx.cond(State.show_advanced, rx.icon("chevron-down", size=14), rx.icon("chevron-right", size=14)),
            rx.cond(State.preset == "custom", "Advanced options (custom)", "Advanced options"),
            variant="ghost",
            size="1",
            color_scheme="gray",
            on_click=State.toggle_advanced,
        ),
        rx.cond(State.show_advanced, advanced_options()),
        rx.text(State.pending_detail, size="2", id="pending-detail"),
        rx.cond(State.index_hint != "", rx.text(State.index_hint, size="1", color_scheme="amber")),
        rx.hstack(
            rx.button(
                "Index it",
                on_click=State.confirm_index,
                loading=State.rebuilding,
                disabled=State.index_hint != "",
                **PRIMARY,
            ),
            rx.button("Cancel", variant="soft", color_scheme="gray", on_click=State.cancel_pending),
        ),
        spacing="2",
        align="start",
        width="100%",
    )


def indexing_view() -> rx.Component:
    return rx.vstack(
        rx.text(rx.cond(State.needs_key_to_index, "Indexing with your key...", "Indexing..."), size="2", weight="medium"),
        rx.progress(value=State.index_percent, max=100, width="100%"),
        rx.text(f"{State.index_done} of {State.index_total}: {State.index_label}", **MUTED),
        spacing="2",
        align="start",
        width="100%",
    )


def upload_card() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.text("2. Add a document", size="2", weight="bold"),
            rx.cond(State.indexing, indexing_view(), rx.cond(State.has_pending, pending_view(), upload_form())),
            rx.cond(State.upload_error != "", rx.text(State.upload_error, size="2", color_scheme="red")),
            spacing="2",
            align="start",
            width="100%",
        ),
        **CARD,
    )


# ---- chat ----


def citation_chip(citation: CitationView) -> rx.Component:
    return rx.button(
        rx.icon("file-text", size=14),
        rx.text(citation.title, size="1", weight="medium"),
        rx.cond(citation.location != "", rx.text(citation.location, size="1", color_scheme="gray")),
        variant="outline",
        color_scheme="gray",
        size="1",
        radius="full",
        on_click=State.open_source(citation.ref),
        title=citation.doc_title,
    )


def answer_image(image: ImageView) -> rx.Component:
    return rx.vstack(
        rx.image(src=image.url, alt=image.description, max_width="420px", width="100%", border_radius="12px", border=BORDER),
        rx.text(image.description, **MUTED),
        spacing="1",
        align="start",
    )


def step_line(step: StepView) -> rx.Component:
    """What the model said, then what it did, so the answer reads as a conversation."""
    return rx.vstack(
        rx.cond(step.note != "", rx.text(step.note, size="2")),
        rx.hstack(
            rx.icon("corner-down-right", size=12, color="var(--gray-9)"),
            rx.text(step.label, size="1", color_scheme="gray"),
            spacing="1",
            align="center",
        ),
        spacing="1",
        align="start",
    )


def live_answer() -> rx.Component:
    """The answer being written: its steps so far, then its text, or "Working..." before any text."""
    return rx.vstack(
        rx.cond(State.live_steps.length() > 0, rx.vstack(rx.foreach(State.live_steps, step_line), spacing="2", align="start")),
        rx.cond(
            State.live_text == "",
            rx.hstack(rx.spinner(size="1"), rx.text("Working...", size="2", color_scheme="gray"), align="center"),
            prose(text=State.live_text),
        ),
        spacing="3",
        align="start",
        max_width="95%",
    )


def assistant_message(message: ChatMessage) -> rx.Component:
    return rx.vstack(
        rx.cond(message.steps.length() > 0, rx.vstack(rx.foreach(message.steps, step_line), spacing="2", align="start")),
        rx.cond(
            message.text != "",
            # Uncited replies (no answer found, errors, or questions about the outline itself) are grey, but
            # still Markdown: a reply about the table of contents can be a real, formatted answer.
            rx.cond(message.found, prose(text=message.text), rx.box(prose(text=message.text), color="var(--gray-11)")),
        ),
        rx.foreach(message.images, answer_image),
        rx.cond(
            message.citations.length() > 0,
            rx.hstack(rx.foreach(message.citations, citation_chip), wrap="wrap", spacing="2"),
        ),
        spacing="3",
        align="start",
        max_width="95%",
    )


def message_view(message: ChatMessage) -> rx.Component:
    return rx.cond(
        message.role == "user",
        rx.box(
            rx.text(message.text, size="3"),
            align_self="flex-end",
            background="var(--gray-4)",
            padding="0.55em 0.95em",
            border_radius="16px",
            max_width="80%",
        ),
        assistant_message(message),
    )


def example_card(question: str, first: bool = False) -> rx.Component:
    return rx.button(
        rx.hstack(
            rx.vstack(
                rx.text("Sample question", size="1", color_scheme="gray"),
                rx.text(question, size="3", weight="medium", text_align="left", white_space="normal"),
                spacing="1",
                align="start",
            ),
            rx.spacer(),
            rx.icon("arrow-right", size=14, color="var(--gray-10)"),
            align="center",
            width="100%",
        ),
        on_click=State.ask_example(question),
        variant="outline",
        color_scheme="gray",
        height="auto",
        width="100%",
        padding="0.9em 1em",
        border_radius="14px",
        background=SURFACE,
        box_shadow="inset 0 0 0 1.5px var(--gray-12)" if first else "inset 0 0 0 1px var(--gray-6)",
        color="var(--gray-12)",
        cursor="pointer",
    )


def empty_chat() -> rx.Component:
    return rx.vstack(
        rx.text("Ask about your documents", **LABEL),
        rx.text(
            "The agent reads each document's table of contents and opens only the sections it needs. "
            "You can see the path it takes under every answer.",
            size="3",
        ),
        rx.cond(
            State.has_samples,
            rx.vstack(
                rx.text("Try one on the sample documents", **LABEL),
                *[example_card(question, first=i == 0) for i, question in enumerate(EXAMPLES)],
                spacing="2",
                align="start",
                width="100%",
            ),
        ),
        rx.cond(State.chat_hint != "", rx.callout(State.chat_hint, icon="info", size="1", color_scheme="gray")),
        spacing="4",
        align="start",
        padding="0.5em 0.25em",
        width="100%",
    )


def composer() -> rx.Component:
    return rx.form(
        rx.hstack(
            rx.input(
                name="question",
                placeholder=rx.cond(
                    ~State.key_set,
                    "Add your API key to chat",
                    rx.cond(State.agent_model == "", "Choose a chat model to start", "Ask a question about the documents"),
                ),
                disabled=~State.can_chat,
                auto_complete="off",
                variant="soft",
                size="3",
                background="transparent",
                flex="1",
            ),
            rx.icon_button(
                rx.icon("arrow-up", size=18),
                type="submit",
                radius="full",
                size="3",
                loading=State.thinking,
                disabled=~State.can_chat,
                title="Send",
                **PRIMARY,
            ),
            align="center",
            width="100%",
            padding="0.4em 0.4em 0.4em 0.9em",
            background=SURFACE,
            border=BORDER,
            border_radius="22px",
        ),
        on_submit=State.ask,
        reset_on_submit=True,
        width="100%",
    )


def chat_panel() -> rx.Component:
    return rx.vstack(
        rx.scroll_area(
            rx.vstack(
                rx.cond(State.messages.length() > 0, rx.fragment(), empty_chat()),
                rx.foreach(State.messages, message_view),
                rx.cond(State.thinking, live_answer()),
                rx.box(id="chat-end"),
                spacing="5",
                width="100%",
                padding="1.25em 1.25em 0.5em",
            ),
            type="auto",
            scrollbars="vertical",
            flex="1",
            width="100%",
            min_height="0",
        ),
        rx.box(
            composer(),
            rx.hstack(
                rx.text("Outline levels per look:", **MUTED),
                rx.select(
                    ["1", "2", "3"],
                    value=State.listing_depth,
                    on_change=State.set_listing_depth,
                    size="1",
                    variant="ghost",
                ),
                rx.text("More levels means fewer agent steps to reach a section.", **MUTED),
                align="center",
                spacing="2",
                padding_top="0.3em",
                padding_left="0.9em",
                wrap="wrap",
            ),
            padding="0.5em 1.25em 1.25em",
            width="100%",
        ),
        spacing="0",
        height=["80vh", "80vh", "100%"],
        min_height="420px",
        min_width="0",
        width="100%",
        background="var(--gray-1)",
    )


# ---- dialogs ----


def outline_row(row: OutlineRowView) -> rx.Component:
    return rx.box(
        rx.button(
            rx.vstack(
                rx.text(row.title, size="2", weight="medium"),
                rx.text(row.summary, size="1", color_scheme="gray", white_space="normal", text_align="left"),
                spacing="0",
                align="start",
            ),
            variant="ghost",
            height="auto",
            on_click=State.open_source(row.ref),
            justify_content="start",
            width="100%",
        ),
        padding_left=row.indent,
        width="100%",
    )


def source_view() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("Source", **LABEL),
            rx.spacer(),
            rx.icon_button(
                rx.icon("x", size=14),
                variant="ghost",
                color_scheme="gray",
                size="1",
                title="Close panel",
                on_click=State.set_source_open(False),
            ),
            align="center",
            width="100%",
        ),
        rx.text(State.source_title, size="4", weight="bold"),
        rx.text(State.source_location, **MUTED),
        rx.cond(State.source_summary != "", rx.callout(State.source_summary, icon="sparkles", size="1", color_scheme="gray")),
        prose(text=State.source_text),
        spacing="3",
        align="start",
        width="100%",
    )


def browse_view() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text("Outline", **LABEL),
            rx.spacer(),
            rx.icon_button(
                rx.icon("x", size=14),
                variant="ghost",
                color_scheme="gray",
                size="1",
                title="Close panel",
                on_click=State.set_browse_open(False),
            ),
            align="center",
            width="100%",
        ),
        rx.text(State.browse_title, size="4", weight="bold"),
        rx.text("Click a section to read it.", **MUTED),
        rx.vstack(rx.foreach(State.browse_rows, outline_row), spacing="1", width="100%"),
        spacing="3",
        align="start",
        width="100%",
    )


def source_panel() -> rx.Component:
    placeholder = rx.center(
        rx.text("Click a citation, or Browse a document, to read it here.", size="2", color_scheme="gray", text_align="center"),
        padding="2em",
        height="100%",
        display=["none", "none", "flex"],
    )
    return rx.box(
        rx.cond(State.source_open, source_view(), rx.cond(State.browse_open, browse_view(), placeholder)),
        id="source-panel",
        padding=rx.cond(State.source_open | State.browse_open, "1.25em", "0"),
        overflow_y="auto",
        min_height="0",
        min_width="0",
        border_left=[BORDER, BORDER, BORDER],
        border_top=[BORDER, BORDER, "none"],
        background=SURFACE,
    )


# ---- page ----


def header() -> rx.Component:
    return rx.hstack(
        rx.center(rx.icon("book-open-text", size=18), width="36px", height="36px", border=BORDER, border_radius="10px", background=SURFACE),
        rx.vstack(
            rx.text("PageIndex RAG", size="3", weight="bold"),
            rx.text("Vectorless RAG: chat by navigating a table of contents", size="1", color_scheme="gray", display=["none", "none", "block"]),
            spacing="0",
            align="start",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("trash-2", size=14),
            "End session and delete my data",
            variant="outline",
            color_scheme="gray",
            radius="full",
            size="1",
            on_click=State.end_session,
        ),
        align="center",
        width="100%",
        padding="0.75em 1.25em",
        border_bottom=BORDER,
        background=SURFACE,
        wrap="wrap",
    )


def left_column() -> rx.Component:
    return rx.vstack(
        key_card(),
        library_card(),
        upload_card(),
        spacing="3",
        align="stretch",
        padding="1em",
        overflow_y="auto",
        min_height="0",
        min_width="0",
        background="var(--gray-2)",
    )


def index() -> rx.Component:
    return rx.box(
        rx.box(
            header(),
            rx.grid(
                left_column(),
                chat_panel(),
                source_panel(),
                columns=rx.breakpoints(initial="1", lg="300px minmax(0, 1fr) 380px"),
                flex="1",
                min_height="0",
                width="100%",
            ),
            display="flex",
            flex_direction="column",
            height=rx.breakpoints(initial="auto", lg="calc(100vh - 2rem)"),
            max_width="1500px",
            width="100%",
            margin="0 auto",
            border=BORDER,
            border_radius="18px",
            overflow="hidden",
            background=SURFACE,
        ),
        padding=rx.breakpoints(initial="0.5em", lg="1rem"),
        min_height="100vh",
    )
