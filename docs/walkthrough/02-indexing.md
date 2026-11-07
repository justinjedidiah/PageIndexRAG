# 02. Indexing: from an uploaded file to a searchable tree

Files covered: everything in `src/pageindex_rag/indexing/` (`docx_extract.py`, `pdf_extract.py`, `markdown_extract.py`, `tree.py`, `pages.py`, `refine.py`, `split.py`, `preview.py`, `options.py`, `estimate.py`, `summarize.py`, `pipeline.py`, `blocks.py` was in file 01).

**Indexing** means turning a document into the tree of sections described in file 01, with a summary on every section. It happens once per document. Asking questions later only reads the result.

It is deliberately done in **two stages**:

```
Stage 1  build_index   (free, local)     file -> blocks -> tree -> refine (merge/fold/split) -> save images -> previews (Free)
         estimate_index (free, local)    count how many Claude calls stage 2 will need  -> shown to the visitor
Stage 2  enrich_index  (costs money)     describe images, then summarize every section  -> uses the visitor's key
Then     store.save_index                write index.json + library.json
```

Everything the visitor pays for is in stage 2, and stage 1 produces the numbers for the "this will cost about N calls, continue?" screen. That is the whole reason for the split.

The one worked example used below: the sample **Meridian Station Operations Handbook**.

---

## Stage 1, step A: read the file into blocks

### `docx_extract.py`

```python
def extract_docx(path: Path) -> list[Block]:
    doc = Document(str(path))
    blocks: list[Block] = []
    for element in doc.element.body.iterchildren():
        if element.tag == qn("w:p"):
            ...
        elif element.tag == qn("w:tbl"):
            ...
    return blocks
```

A `.docx` file is a zip of XML files. The `python-docx` library opens it. `_read` walks the main body **in document order**: each child is a paragraph (`w:p`), a table (`w:tbl`) or a **content control** (`w:sdt`, the boxes Word templates use for cover pages, forms and the table of contents), whose content is read the same way, as if the box were not there. `qn("w:p")` converts the short prefix form into the full namespaced XML name that the library uses internally, so the comparison `element.tag == ...` works.

Reading in order matters: the order of blocks is the order of the document, and the tree builder depends on it.

**Paragraph branch** (`_read_paragraph`)

1. Any images inside the paragraph are added first.
2. Its text is read by `paragraph_text`, not python-docx's `paragraph.text`, which only reads plain runs and links and so misses text inside an inline content control or a tracked insertion. `paragraph_text` takes every piece of text (`w:t`, tabs, line breaks) except formatting, pictures and text boxes (a text box is stored twice for compatibility, so reading it would repeat it). Deleted tracked text is stored differently (`w:delText`) and so is left out: the document is read as it now stands. Blank paragraphs are skipped (an image-only paragraph still gave its image above).
3. A **table-of-contents entry** (inside Word's ToC box, or styled "toc 1", "toc 2"...) becomes a `TocLine` (title, level, page), not text. See below.
4. Otherwise, if Word gives the paragraph an **outline level**, it becomes a `Heading`; if not, a plain `Paragraph`.

```python
def _outline_level(paragraph):
    value = _outline_value(paragraph._p.pPr)          # set on the paragraph itself?
    style = paragraph.style
    while value is None and style is not None:      # else on its style, or the style that one is based on
        value = _outline_value(style.element.pPr)
        if value is None and _HEADING_STYLE.match(style.name):  # fallback: a "Heading N" style without one
            return int(...)
        style = style.base_style
    return None if value is None or value >= 9 else value + 1
```

This is the key to "Word has a ready-made structure". Word gives each paragraph an **outline level**: 0-8 for heading levels 1-9, or 9 for body text. The built-in *Heading 1 / 2 / 3* styles carry one, and so do most custom heading styles (a template's "Chapter Title", or any style "based on" a heading style). These are exactly the paragraphs Word's **Navigation Pane** lists, and what Word builds its own table of contents from, so the rule is easy to explain: **if Word's Navigation Pane shows your headings, the app does too.** An outline level set on a single paragraph wins over its style's, so a heading-styled paragraph set to "body text" is body text. The **Title** style has no outline level, so the big title line at the top becomes ordinary text: in the samples, the root's own text.

**Word's table of contents**

A table of contents inserted by Word (References > Table of Contents) sits in a content control marked "Table of Contents" (`_is_toc_control`), and its entries use the styles "toc 1", "toc 2"... (or just those style ids). Each entry becomes a `TocLine(level, title, page)`: "Installation⇥12" gives title "Installation", page "12". The box's own title ("Contents", which has no page number) is dropped. The tree builder skips `TocLine` blocks, so the ToC never ends up as text; the pipeline keeps the entries as the document's own ToC (`source_toc`, with pages). The ToC is not used to build the tree: it was generated *from* the headings, which already give the outline with exact positions.

**A typed contents page** (`typed_toc.py`)

Many documents have a contents page typed by hand ("Installation ........ 12") and no heading styles. `apply_typed_toc` runs on the extracted blocks right after extraction, so it works the same for any file type:

1. **Find it.** Near the start (within the first fifth of the document, or the first 40 blocks), a run of at least 3 lines that each end in a page number after a tab, dot leaders or a gap; roman page numbers count. A line without a page number followed by one with joins it (a wrapped title). A "Contents" line just above is removed with it.
2. **Levels.** From numbering if most entries are numbered ("1.2" is level 2), else from indentation (the `indent` the Word reader records: the paragraph's left indent plus leading tabs or spaces), else all level 1.
3. **Find each entry in the body, in order**, searching forward from the last one found: a paragraph of at most 150 characters with the same text, ignoring case, punctuation and leading numbering ("1 Getting started" matches "Getting started"). Searching in order keeps a sentence that only mentions a title, or a second section with the same name, from being taken for it. Page numbers are ignored: a .docx has no fixed pages.
4. **Trust it** only if at least 2 entries, and at least half of them, were found. A price list ("Panel ..... 200") or a contents page too out of date to follow fails this, and nothing changes.

A **list of figures or tables** looks just like a contents page but points at captions, so a run under such a title is skipped, and so are entries that start with "Figure" or "Table".

A trusted contents page is removed from the text and its entries kept as `TocLine` blocks (unless Word's own ToC is already kept). If the document **has headings**, that is all: the headings still build the outline. If it **has none**, the paragraphs the entries point to become `Heading` blocks at the entries' levels, and the contents page has built the outline.

**Headings formatted by hand** (`heading_looks.py`)

The last try, only when a document has no headings and no trustworthy contents page: many people make headings by hand, bold or bigger or numbered. For this the Word reader also records how each paragraph looks: whether all its text is bold, the font size most of it uses (from the text's own formatting, else its character style, else the paragraph's style chain, else the document's default), and whether it is a Word list item. `apply_heading_looks`:

1. **Candidates:** short lines (12 words, 100 characters), mostly words rather than numbers (at least one real word and no more number-like tokens than words, so a chart's axis labels never qualify), that do not end like a sentence (a question mark or colon is fine) and stand out: all bold, at least 15% bigger than the document's normal size (the size most of its text uses), numbered like a section ("1.2", "Chapter 3", "A."), or in capitals. Captions ("Figure 3") and Word list items are not candidates: Word's automatic numbering is rarely a heading.
2. **Group them by look**: size, bold, capitals, question or not.
3. **Keep only looks that recur**: at least 3 lines (2 in a document under 30 paragraphs) with text between them. More than 3 lines of one look in a row with nothing between are a list (lines numbered at different depths, like "1 Setup" then "1.1 Tools", may follow each other: that is nesting). Headings are a pattern that repeats through a document; a pull quote, a warning or one big title at the top is not, and stays text. This is also what makes bold questions work: spread through the document they are an FAQ's sections, once or twice they are quotes.
4. **Give up** if the kept lines hold more than 30% of the text, or are more than two thirds of the paragraphs: the document just uses a lot of bold. Measured by text rather than by paragraphs because in an FAQ (a question, then a one-paragraph answer) half the paragraphs are headings, yet they are a small part of the text.
5. **Levels**: from numbering if most are numbered "1.2"-style, else by look (biggest first, then bold, then capitals), at most 3.

**Sub-headings inside a big section** (`apply_section_looks`)

A document with headings can still have one huge section whose parts are only bold lines. When splitting is on (it is part of "split sections too big to read in one go"), each section whose own text, up to the next heading, is over `max_leaf_tokens` gets the same detector run on just its paragraphs, with two differences: a look must appear **at least twice inside that section** (with text between), and the lines it finds go **one level below the section** (two looks: one and two levels below; never deeper). A single bold line, such as a warning in the middle, stays text: counting only inside the section stops it from splitting the section into "before" and "after". The normal text size is still the whole document's, so a section printed in a bigger font throughout does not look like all headings. Numbered lines count relative to the shallowest found, so inside "3 Setup", "3.1" and "3.1.1" are one and two levels below it. The text before the first sub-heading stays the section's own; anything still too big is then cut by size as before. The lines found are the document's own titles, so they are `original` nodes and a summarizer never renames them.

Both uses share one function, `find_heading_looks(blocks, paragraphs, normal_size, min_repeats, first_level, max_levels)`; `apply_heading_looks` (the whole document) and `apply_section_looks` (each big section) only choose those settings.

`build_index` records where the tree came from in `structure_source`: `"headings"`, `"typed_toc"`, `"formatting"`, or `"none"` when nothing gave one (the text is then cut by size). The Browse panel and `pageindex tree` show it, so a flat outline explains itself.

**Images**

```python
def _images_in(element, doc):
    images = []
    for blip in element.iter(qn("a:blip")):
        part = doc.part.related_parts.get(blip.get(qn("r:embed")))
        if part is not None:
            images.append(Image(data=part.blob, ext=part.partname.ext.lower()))
    return images
```

Inside the XML, a picture is a `blip` element that points to the image by a relationship id (`r:embed`). `related_parts` maps that id to the stored file; `.blob` is its bytes and `.partname.ext` its extension. Each becomes an `Image` block.

**Tables become Markdown text**

```python
def _table_to_markdown(table):
    rows = [[_cell_text(cell.text) for cell in row.cells] for row in table.rows]
    if not rows: return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = [_row(rows[0]), _row(["---"] * width)]
    lines.extend(_row(row) for row in rows[1:])
    return "\n".join(lines)
```

Rows of cell text; pad shorter rows to the widest row; the first row is the header, then a `| --- | --- |` separator row, then the rest. `_cell_text` escapes a literal `|` (which would break the table) and flattens line breaks. The result for the handbook's module table:

```
| Module | Purpose | Crew capacity |
| --- | --- | --- |
| Habitat 1 | Sleeping quarters and galley | 4 |
```

Why Markdown? A table is just text to the AI, and Markdown keeps rows and columns readable to both Claude and the browser (which renders it as a real table).

### `pdf_extract.py`

A PDF has no paragraphs, only text drawn at positions on pages. Two libraries with permissive licences read it (PyMuPDF was ruled out for its AGPL licence):

- **pypdfium2** (Chrome's PDF engine) opens the file first. A password-protected file fails here and becomes a `DocumentRejected` ("This PDF is password-protected..."). It also gives the page count (checked against `max_pdf_pages`, 300 by default, *before* any text is read: a small file can hold thousands of text pages), each page's **printed label** (`get_page_label`: "iv", "12"), and the **bookmarks** (`get_toc`: level, title, target page and the point on it).
- **pdfplumber** gives each page's lines with every character's font name and size (`extract_text_lines(return_chars=True)`).

`_lines` turns each line into `_Line(text, top, bottom, x0, size, bold)`: size is the median character size; bold means every character's font name says Bold, Black, Heavy or Semibold. `_paragraphs` groups lines: a new paragraph starts after a gap wider than half the line height, where the line moves up the page (a new column), or where size or boldness changes. `_join_lines` puts a word hyphenated at the line end back together ("main-" + "tenance"). Each paragraph becomes a `Paragraph(text, indent=x0, bold, size, page)`, the same shape as a Word paragraph, so the typed contents page and heading-look detectors work on PDFs unchanged.

Before lines become paragraphs, `_drop_running_lines` removes **running headers and footers**: a line in the top or bottom tenth of the page goes when its text, with numbers removed, recurs on at least 3 pages and a fifth of them (so "10 Annual Report 2026" and "Annual Report 2026 11" count as one footer), or when it is only a page number ("- 4 -", "Page 4 of 9"). Without this a footer reads as a recurring bold line and becomes a run of fake sub-headings.

`_check_text_layer` refuses a PDF where no page, or fewer than half the pages, has 20 characters of text: those are scans, and there is no OCR.

**Bookmarks as the outline.** `_believable` keeps bookmarks with a title and a target page, dropping junk like "Page 3", "12", "Untitled"; they are used only if at least 2 remain and they are at least half of all bookmarks. `_page_blocks` places each bookmark's `Heading` on its page: at the first paragraph (at or below the point the bookmark targets) whose text starts with the bookmark's title (or up to 3 paragraphs in a row that together do, since a title like "Box A: Global supply chains" is often printed as a small "Box A" over a bigger subtitle), which it replaces (a heading run into its first line, "Cleaning. Use a soft brush", is split into heading and paragraph). If no line carries the title, the heading is inserted where the bookmark points. The used bookmarks also become `source_toc`, with printed page labels, and `structure_source` is `"bookmarks"`.

`pdf_cache.read_pdf` saves what was read as `<file>.read.json` next to the PDF: reading is the slow part (about 0.07 s a page, 21 s for 300 dense pages), and every change of a structure option, and "Index it" itself, rebuild the tree from the same file. The saved read sits in the visitor's own uploads folder, so it is deleted with the session; nothing is kept in shared memory. It is used only for the file beside it and only if the SHA-256 of that file's bytes (and the page limit and a format number) match the ones saved with it, so a changed file is always read again, and a damaged saved read is ignored. The app deletes it once the document is indexed, cancelled or refused (`pdf_cache.forget`).

Not handled yet: text inside charts (axis labels and legends come through as text), multi-column order beyond "a new column starts a paragraph", tables (their rows come through as lines of text) and images.

### `markdown_extract.py` (developer use only)

Does the same job for `.md` files: it reads line by line, toggling an `in_fence` flag at ``` lines so `#` characters inside code blocks are not mistaken for headings; a regex `^(#{1,6})\s+(.+?)\s*#*\s*$` recognises `#` headings and counts the `#`s for the level; blank lines end a paragraph (`flush`); and `![alt](file.png)` images are loaded from next to the file. The web page refuses Markdown; this exists so the tests and command line can run without Word.

---

## Stage 1, step B: build the tree (`tree.py`)

```python
def build_tree(blocks, title, kind) -> tuple[Node, list[tuple[str, Image]]]:
    root = Node(id="root", title=title, level=0, location=Location(kind=kind))
    stack = [root]
    parts: dict[str, list[str]] = {root.id: []}
    images = []
```

- `root` is the document itself (its title is the file name without extension).
- `stack` is the key idea: **the chain of currently-open sections**, from the root down to wherever we are now.
- `parts` collects each node's text pieces (a list per node id), joined at the end.

Then it loops over the blocks:

```python
if isinstance(block, Heading):
    while stack[-1].level >= block.level:
        stack.pop()
    parent = stack[-1]
    prefix = "" if parent.id == "root" else f"{parent.id}."
    node = Node(id=f"{prefix}{len(parent.children) + 1}", title=block.title, level=block.level,
                location=Location(kind=kind, heading_path=[*parent.location.heading_path, block.title]))
    parent.children.append(node)
    stack.append(node)
    parts[node.id] = []
```

**How a heading finds its parent.** Close every open section that is at the *same or deeper* level than this heading (`stack.pop()` while `stack[-1].level >= block.level`); whatever is left on top of the stack is the parent. Trace through the handbook:

| Block | Stack before | Action | New id |
|---|---|---|---|
| Heading 1 "Overview" | `[root]` | root.level 0 < 1, no pops; parent = root | `1` |
| Heading 2 "Mission" | `[root, Overview]` | Overview.level 1 < 2; parent = Overview | `1.1` |
| Heading 2 "Station layout" | `[root, Overview, Mission]` | Mission.level 2 >= 2: pop; parent = Overview | `1.2` |
| Heading 1 "Crew and Roles" | `[root, Overview, Station layout]` | pop both (2 >= 1, then 1 >= 1); parent = root | `2` |

The id comes from the parent's id plus "which child number am I" (`len(parent.children) + 1`). The heading path for citations is the parent's path plus this title (`[*a, b]` builds a new list). `root` is never popped because its level (0) is never >= a heading's level (>= 1).

If a document skips a level (a Heading 1 followed directly by a Heading 3), the Heading 3 simply nests under the Heading 1, because the stack only compares levels, it does not need them to be consecutive.

```python
elif isinstance(block, Image):
    image_id = f"img-{len(images) + 1}"
    images.append((image_id, block))
    parts[stack[-1].id].append(image_placeholder(image_id))
elif isinstance(block, Paragraph):
    parts[stack[-1].id].append(block.text)
else:
    parts[stack[-1].id].append(block.markdown)   # a Table
```

Everything that is not a heading is attached to the **section on top of the stack** (the most recent heading). An image gets a document-wide numbered id and leaves a placeholder in the text (see file 01). Content that appears before the first heading attaches to the root, because the stack is still `[root]`.

```python
for node in root.walk():
    node.text = "\n\n".join(parts[node.id])
return root, images
```

Finally each node's text is its pieces joined by blank lines. It returns the tree plus the list of images with their ids (the raw image bytes are saved later).

---

### Pages in PDF sections (`pages.py`)

Each PDF block carries the page it is on. `build_tree` writes a page marker, `[page:N]`, into the text wherever the page changes (including just before a heading that opens a new page). The markers travel with the text through merge, fold and split, which only move text around in document order; the splitter keeps a marker-only paragraph with its neighbour, like an image placeholder. After refining, `resolve_pages` walks the tree in document order, carrying the current page, and sets each node's `pdf_page_start`/`pdf_page_end` and printed labels: a section starts at its first marker or first text, and ends where its last text, or its last subsection, ends. The markers are then removed, before previews, summaries or the agent see the text. `Location.label()` shows the printed numbers: "p. 3", "pp. 1-2".

## Stage 1, step C: refine the tree (`refine.py`, `split.py`, `preview.py`)

The tree from step B is the document's own outline. Before the agent can use it well, two problems can need fixing:

- **Sections too big to read.** An agent asking to read a 40-page section would waste its whole budget.
- **Too many tiny stops.** The agent sees one level of the tree per tool call, so every extra level, and every three-line section it has to open and leave again, costs a round trip out of a budget of about 15 steps.

The rule is to **change the original outline only where a section breaks a limit**. A good outline comes back exactly as it was. Every change below is free (no LLM) and can be switched off with an option (see "Options and presets" further down).

```python
def refine_tree(root, options):
    if options.small_sections == "fold":
        for child in root.children:
            _fold(child, options.max_leaf_tokens)
    if options.small_sections != "off":
        _collapse_chains(root, options.min_leaf_tokens)
        _merge_tiny_leaves(root, options.min_leaf_tokens, options.max_leaf_tokens)
        _collapse_chains(root, options.min_leaf_tokens)  # a merge can leave a section with one child
    if options.split_large:
        split_large_nodes(root, options.max_leaf_tokens)
```

Small sections are handled first and big ones split last, because a merge never makes a section bigger than one read, so nothing merged ever needs splitting again.

### Counting size

```python
def estimate_tokens(text): return len(text) // 4
```

A **token** is the unit AI models read (roughly 3/4 of a word). Counting them exactly needs the model's own tokenizer; `len(text) // 4` is the rule of thumb "about 4 characters per token" for English. Good enough to decide "is this big?". The two thresholds are `max_leaf_tokens` (default 2000, about 8,000 characters: the most one `read_node` should return) and `min_leaf_tokens` (default 150: below this a section is "tiny").

### Small sections: merge (the default) and fold

**Merge tiny siblings.** A run of two or more neighbouring sections that have no subsections and are each under `min_leaf_tokens` becomes one section, as long as the result still fits in one read. Under "Parts", the tiny "Bolt" and "Nut" become one section titled `Bolt · Nut`, whose text keeps each original title on its own line:

```
Bolt
M6 steel.

Nut
M6 nylon.
```

It keeps the first section's id (`1.1`), is marked `origin="generated"`, and its `Location.covers` lists the merged titles, so a citation reads "Parts > Bolt, Nut".

**Collapse single-child chains.** A section with exactly one subsection and almost no text of its own is just a hallway: the agent would spend a call walking through it. It absorbs the subsection and is titled `Safety › Rules`. When the absorbed child is a merge of the section's own subsections, the section keeps its own title and takes over their titles in `covers`. That is why the Meridian sample now lists "Overview (covers Mission, Station layout)" as one readable section.

The root is special: it keeps its id and the document's title, and only absorbs a lone child that has subsections of its own. That removes the common "one big heading at the top" level, but a document always keeps an outline to browse.

**Fold** (`small_sections="fold"`) does all of that, and also turns any part of the outline small enough to read in one go into a single section, with its sub-headings kept as lines in the text. It works top-down, so it folds the largest piece that fits. The root is never folded.

`small_sections="off"` keeps the outline exactly as the file has it.

### Big sections: split (`split.py`)

```python
def split_large_nodes(node, max_tokens):
    for child in node.children:
        split_large_nodes(child, max_tokens)       # handle the whole tree
    if estimate_tokens(node.text) <= max_tokens:
        return                                      # small enough, leave it
    chunks = split_text(node.text, max_tokens)
    ...
    node.text = ""
    node.children = [*generated, *node.children]
```

If a node's own text is over `max_leaf_tokens`:

1. Cut the text into chunks (`split_text`).
2. Make a **new child node per chunk**, id like `2.1.p1`, `2.1.p2`, marked `origin="generated"`. Each copies the parent's `Location` so citations still point to the real heading.
3. Empty the parent's own text and put the new children **before** its existing children (the text came before the subsections in the document).

A document with no headings at all is just a root with a lot of text, so it becomes one level of parts directly under the root. It never gets deeper than that.

`split_text` cuts at the best boundary available:

- **Between paragraphs** first.
- A paragraph that alone is too big is cut **between sentences** (`(?<=[.!?])\s+`: a split after `.`, `!` or `?` followed by whitespace).
- A sentence that is still too big (a run-on list, a URL dump) gets a **hard cut** at the limit, backing off to the last space.
- A Markdown **table** that is too big is cut **between rows**, and every piece repeats the header and separator rows, so each piece is still a readable table.
- A paragraph that is only an image placeholder is attached to the paragraph after it, so an image is never a part on its own.

Then `_pack` groups the pieces into chunks. It first works out how many chunks the limit needs (`ceil(total / max)`) and aims each chunk at an equal share. A greedy "fill until full" packing would give 150 + 50 for 200 tokens with a limit of 150; this gives two chunks of about 100.

Each part gets a **free title** from its first sentence, for example `Engines: The port engine is serviced every 400 hours.` (a table part gets `Engines: table (Part, Count)`). With LLM summaries on, stage 2 replaces it with a descriptive title. Merged and folded sections keep theirs, since they are made of real headings.

### Free previews (`preview.py`)

With the Free preset there is no stage 2, so nothing would fill in summaries. `fill_previews` gives every node a **preview** instead: its first sentences (3 by default, about 300 characters at most, image placeholders removed, a table shown as `[Table: Name, Role]`), followed by `Covers: A, B, C.` when it has subsections. `Node.summary_source` records `"preview"`, and the agent's listing marks such summaries `(preview)` so the model knows it is only the start of the section.

### Options and presets (`IndexOptions` in `models.py`, presets in `options.py`)

Everything above, plus what stage 2 does, is one record:

| Option | Default | Meaning |
|---|---|---|
| `split_large` | on | split sections bigger than `max_leaf_tokens` (2000) |
| `small_sections` | `merge` | `off`, `merge` or `fold`, with `min_leaf_tokens` (150) |
| `summaries` | `llm` | `llm` (Claude) or `preview` (free) |
| `summary_length` | `detailed` | `short` (1-2 sentences) or `detailed` (2-4) |
| `describe_images` | on | Claude describes each image |

The three presets only change the last three, since the tree settings cost nothing: **Free** (previews, no images: no LLM call at all, no key needed), **Budget** (short summaries, no images) and **Full** (detailed summaries and images, the default). The web page and the CLI start from a preset and let you change any single option on top. The index stores the options it was built with in `DocumentIndex.options`.

---

## Stage 1, step D: put it together (`pipeline.build_index`)

```python
def build_index(path, store, settings, options=None) -> DocumentIndex:
    options = options or preset_options(settings=settings)      # the Full preset
    kind, blocks = _extract(path)
    doc_id = make_doc_id(path)
    root, raw_images = build_tree(blocks, title=path.stem, kind=kind)
    source_toc = [TocEntry(title=b.title, level=b.level) for b in blocks if isinstance(b, Heading)] or None
    refine_tree(root, options)
    if options.summaries == "preview":
        fill_previews(root, options.preview_sentences)
    images = [ImageRecord(id=image_id,
                          file=store.save_image(doc_id, image_id, image.data, image.ext),
                          mime=_MIME_TYPES.get(image.ext, f"image/{image.ext}"),
                          description=image.alt)
              for image_id, image in raw_images]
    index = DocumentIndex(doc_id=doc_id, title=path.stem, source_path=path.name, kind=kind,
                          source_toc=source_toc, root=root, images=images,
                          summary=root.summary, options=options)
    _assign_image_nodes(index)
    return index
```

Line by line:

1. `_extract` picks the reader by file extension. For `.pdf` it raises `NotImplementedError("PDF support is stage 3")`. This is the *right* use of that error: a feature genuinely not written yet. (The web page checks for PDFs earlier and shows a friendly message instead of letting this fire.)
2. `make_doc_id` (below) names the document.
3. Tree, then `source_toc` (the heading list, kept as reference; `or None` turns an empty list into `None`), then refining, then previews if the options ask for them.
4. Each raw image is **written to disk right now** via `store.save_image`, and the `ImageRecord` remembers the relative path. `description=image.alt` seeds the description with the file's own alt text if it has any.
5. `_assign_image_nodes` runs last, **after** refining, so an image that moved into a generated part (or into a merged or folded section) is linked to the node that holds it now:

```python
def _assign_image_nodes(index):
    records = {record.id: record for record in index.images}
    for node in index.root.walk():
        for image_id in IMAGE_REF.findall(node.text):
            if image_id in records:
                records[image_id].node_id = node.id
                records[image_id].location = node.location
```
For every node, find the `[image:...]` markers in its text and point those images at it.

```python
def make_doc_id(path):
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")[:60] or "doc"
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    return f"{slug}-{digest}"
```
The id is a readable slug of the file name plus the first 8 characters of a **hash of the file's contents** (a fingerprint). Consequences: the same file always gets the same id (so uploading it twice is detected as a duplicate), and a changed file gets a new id. The slug is restricted to `a-z0-9-` which also makes the id safe to use as a folder name and in URLs. (SHA-1 is used purely as a fingerprint here, not for security.)

Note that images are on disk after `build_index` even though no `index.json` exists yet. If the visitor cancels at the cost screen, the app deletes that document folder.

---

## Between the stages: `estimate.py`

```python
def estimate_index(index, store, options=None) -> Estimate:
    options = options or index.options or IndexOptions()
    sections = list(index.root.walk())
    summaries = len(sections) if options.summaries == "llm" else 0
    ...   # input tokens per summarized section, as below
    images = 0
    if options.describe_images:
        images = sum(should_describe(...) for record in index.images if ...)
    return Estimate(sections=len(sections), summaries=summaries, images=images, calls=summaries + images,
                    input_tokens=input_tokens + images * IMAGE_INPUT_TOKENS,
                    output_tokens=summaries * OUTPUT_TOKENS[options.summary_length] + images * IMAGE_OUTPUT_TOKENS)
```

It mirrors what stage 2 will do **with the given options**. **One Claude call per section** when summaries are on, plus **one per describable image** when image descriptions are on. The Free preset gives zero calls. The web page calls it once per preset on the same tree, to show what each would cost. Short summaries are charged fewer output tokens (`OUTPUT_TOKENS = {"short": 60, "detailed": 110}`). For each section the input is: a fixed instruction overhead (about 250 tokens) + the section's own text (cut at 12,000 characters like the real prompt) + about 70 tokens for each child's summary (a parent's prompt includes its children's summaries). Images are charged a flat guess of 1,500 input tokens. The number constants at the top (`250`, `70`, `110`, `1500`, `80`) are **estimates, not measurements**; that is why the page says "roughly". `Estimate` is a frozen dataclass (immutable result record).

Known gap: the cut-off `12000` appears twice (here as `MAX_SECTION_CHARS`, and as the default of `summarize_tree`'s `max_chars`). They are meant to match but nothing enforces it.

`calls` is also reused as the **total for the progress bar**, so the bar reaches 100% exactly when the last call finishes.

---

## Stage 2: the LLM work (`summarize.py` and `pipeline.enrich_index`)

### Structured output: getting data back instead of prose

```python
class _NodeSummary(BaseModel):
    title: str = Field(description="Short descriptive title for the section.")
    summary: str = Field(description="2-4 sentences: what the section covers and which questions it can answer. Mention any tables or images.")

class _ImageDescription(BaseModel):
    description: str = Field(description="One or two sentences describing what the image shows.")
    tags: list[str] = Field(description="A few short keywords for finding this image.")
```

```python
model = llm.with_structured_output(_NodeSummary)
result = model.invoke(prompt)       # result is a _NodeSummary object
```

`with_structured_output` tells Claude "reply with data shaped like this class". The `Field(description=...)` text is sent to Claude as instructions for each field. The reply comes back already parsed, so there is no fragile text-scraping. The leading underscore on the class names marks them as internal to this module.

### Which images are worth describing

```python
def should_describe(record, data) -> bool:
    if record.mime not in SUPPORTED_IMAGE_MIMES: return False
    try:
        with Image.open(BytesIO(data)) as image:    # reads the header only
            width, height = image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False
    return min(width, height) >= MIN_IMAGE_SIDE      # 32 pixels
```

Skip anything Claude cannot read (Word's EMF/WMF vector formats), anything that is not a valid image, and anything with a side under 32 pixels (icons, bullets, thin divider lines). **Pixels, not file size**: an earlier version skipped files under 4 KB, which silently dropped a perfectly good flat-colour chart (a simple chart compresses to a tiny file). `Pillow` (the `PIL` import) opens only the header to read the size, so this is cheap.

```python
def prepare_for_vision(data) -> tuple[bytes, str] | None:
    with Image.open(BytesIO(data)) as image:
        image.thumbnail((MAX_VISION_SIDE, MAX_VISION_SIDE))      # shrink to fit 1568x1568, only ever smaller
        buffer = BytesIO()
        if image.mode in ("RGBA", "LA", "P"):                    # has transparency / palette
            image.convert("RGBA").save(buffer, "PNG"); mime = "image/png"
        else:
            image.convert("RGB").save(buffer, "JPEG", quality=85); mime = "image/jpeg"
    return (buffer.getvalue(), mime) if buffer.tell() <= MAX_VISION_BYTES else None
```

Before sending, shrink big images (Claude's recommended maximum is about 1568 px on the long side, and the API rejects files over 5 MB). `thumbnail` keeps the aspect ratio. Images with transparency stay PNG; others become JPEG at quality 85. If it is still over 5 MB, return `None` and the image is skipped. `BytesIO` is an in-memory file so nothing touches the disk.

### `describe_images`: one call per image, in parallel

```python
def describe_images(images, doc_dir, llm, workers=6, on_step=None):
    model = llm.with_structured_output(_ImageDescription)

    def describe(record):
        data = (doc_dir / record.file).read_bytes()
        prepared = prepare_for_vision(data) if should_describe(record, data) else None
        if prepared is None:
            return
        data, mime = prepared
        hint = f" The document's alt text for it is: {record.description}" if record.description else ""
        message = HumanMessage(content=[
            {"type": "text", "text": f"Describe this image from a document so it can be found by search.{hint}"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
        ])
        result = model.invoke([message])
        record.description = result.description
        record.tags = result.tags
        if on_step: on_step(f"Described image {record.id}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(describe, images))
```

- `describe` is a **closure**: a function defined inside another function that can see its variables (`model`, `doc_dir`, `on_step`).
- The message to Claude has two parts: instruction text and the picture, sent as a **data URL** (`data:image/png;base64,....`), which is how an image is embedded in a request without hosting it anywhere. `base64` turns the raw bytes into text characters.
- `ThreadPoolExecutor` runs up to `workers` (6) calls **at the same time**. Waiting for the network is the slow part, so doing six at once makes indexing several times faster. `pool.map` starts them; wrapping it in `list(...)` waits for all and **re-raises the first error** (without it, errors inside threads would vanish).
- Each thread modifies a *different* `record`, so there is no clash.

### `summarize_tree`: bottom-up, level by level

```python
levels = defaultdict(list)
pending = [(root, 0)]
while pending:
    node, depth = pending.pop()
    levels[depth].append(node)
    pending.extend((child, depth + 1) for child in node.children)
```
Group every node by its depth in the tree: `{0: [root], 1: [Overview, Crew..., ...], 2: [Mission, ...]}`.

```python
with ThreadPoolExecutor(max_workers=workers) as pool:
    for depth in sorted(levels, reverse=True):
        list(pool.map(summarize, levels[depth]))
```
Go from the **deepest level up to the root**. All nodes on one level are summarized in parallel (they do not depend on each other). A level only starts when the one below it has finished. This ordering is the whole design: a parent's summary is written from its **children's finished summaries**, so by the time the root is summarized it has a condensed picture of the entire document.

```python
def summarize(node):
    result = model.invoke(_prompt(node, descriptions, max_chars))
    node.summary = result.summary
    if node.origin == "generated":
        node.title = result.title
    if on_step: on_step(f'Summarized "{node.title}"')
```
Only **generated** nodes take Claude's title (to replace "Roster (part 1 of 2)"); original headings keep the author's own.

```python
def _prompt(node, descriptions, max_chars) -> str:
    text = IMAGE_REF.sub(lambda m: f"[image: {descriptions.get(m.group(1)) or 'no description'}]", node.text)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[...truncated]"
    children = "\n".join(f"- {child.title}: {child.summary}" for child in node.children) or "(none)"
    return ("You are indexing a document so that an agent can find information by reading section "
            "summaries without opening the sections.\n\n"
            f"Section title: {node.title}\n\n"
            f"Section text:\n{text or '(no text of its own)'}\n\n"
            f"Subsections:\n{children}\n\n"
            "Write the summary. For the title, keep the existing one unless it is a generic placeholder "
            "like 'part 2 of 3', in which case write a descriptive one.")
```

The exact text Claude receives. Image markers are swapped for their descriptions, so a section containing a chart is summarized as "...includes a bar chart of oxygen reserves". Very long text is cut at 12,000 characters. Children are listed as `- title: summary`. The sentence "so that an agent can find information by reading section summaries" is the most important line: it tells Claude *what the summary is for*, so it writes "what questions can this section answer" rather than a generic abstract.

### `pipeline.enrich_index`: running stage 2 with progress

```python
def enrich_index(index, store, settings, api_key=None, on_progress=None, options=None):
    from ..llm import chat_model
    from .summarize import SUMMARY_MAX_TOKENS, describe_images, summarize_tree

    options = options or index.options or preset_options(settings=settings)
    index.options = options
    total = estimate_index(index, store, options).calls
    done = 0
    lock = threading.Lock()

    def on_step(label):
        nonlocal done
        with lock:
            done += 1
            if on_progress:
                on_progress(done, total, label)

    if options.describe_images:
        describe_images(index.images, store.doc_dir(index.doc_id), chat_model(settings.vision_model, api_key), ...)
    if options.summaries == "llm":
        summarize_tree(index.root,
                       chat_model(settings.index_model, api_key, max_tokens=SUMMARY_MAX_TOKENS[options.summary_length]),
                       index.images, settings.index_workers, on_step, length=options.summary_length)
        index.summary = index.root.summary
```

Each pass runs only if the options ask for it. `summary_length="short"` picks a schema whose summary field asks for one or two sentences, adds "in one or two short sentences" to the prompt, and caps the reply at 256 tokens (512 for detailed). The caps only stop a runaway reply; the prompt sets the length.

- The imports are **inside the function** on purpose: the tests replace `pageindex_rag.llm.chat_model` with a fake and that only works if it is looked up at call time; and it avoids loading the Anthropic libraries when no AI call is being made.
- `on_step` is called from the worker threads, possibly at the same moment, so the counter is protected by a **lock** (`with lock:` lets one thread at a time through). `nonlocal done` lets the inner function change the outer variable.
- Images are described **first**, then summaries, because the summaries use the image descriptions.
- The vision model and the summary model come from settings (both Haiku by default); the visitor's `api_key` is passed to both. Finally the document's `summary` is a copy of the root's.

### `index_document`: the two stages in one call

```python
def index_document(path, store, settings, use_llm=True, api_key=None, on_progress=None, options=None):
    options = options or preset_options(settings=settings)
    if not use_llm:
        options = options.model_copy(update={"summaries": "preview", "describe_images": False})
    index = build_index(path, store, settings, options)
    if options.uses_llm:
        enrich_index(index, store, settings, api_key, on_progress, options)
    store.save_index(index)
    return index
```
Used by the command line, the sample builder and the tests. The web page calls the two stages separately so it can show the cost in between. `use_llm=False` turns the options into free ones (previews instead of summaries, no image descriptions), so stage 2 is skipped: many tests use this.

---

## Things that are not built, or limited

1. **A Word file without headings (no outline levels) gets no real structure.** A typed contents page or lines formatted like headings are used if it has them (see above); only a document with neither gets no real structure. All its text lands under the root and is only cut by size into balanced parts (one level, titled by their first sentence). The design intends to have Claude *generate* a table of contents for such files, and optionally pick topic boundaries when splitting; **neither is implemented yet**. Both are planned together with PDF.
2. **PDF reading is basic** (see `pdf_extract.py`): no header/footer removal, tables or images yet, and no page view in the source panel.
3. Word features that are ignored: headers and footers, footnotes, text boxes, nested tables, content controls inside table cells. Merged table cells may repeat their text.
4. The token estimate is a rough rule (characters / 4).
5. Splitting is by size, not by topic: a cut can fall between two sentences that belong together.
6. If any one Claude call fails (a bad key, a rate limit), the **whole indexing run stops** and the half-built document is deleted. There is no "resume". (The Anthropic library already retries transient errors a couple of times by itself.)
7. Six parallel calls could exceed a small account's rate limit; the number is configurable (`PAGEINDEX_INDEX_WORKERS`).
8. How *good* Haiku's summaries are on real documents has not been tested; that is the next thing to look at with your real key.
