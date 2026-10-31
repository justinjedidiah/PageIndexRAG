# 02. Indexing: from an uploaded file to a searchable tree

Files covered: everything in `src/pageindex_rag/indexing/` (`docx_extract.py`, `markdown_extract.py`, `tree.py`, `split.py`, `estimate.py`, `summarize.py`, `pipeline.py`, `blocks.py` was in file 01).

**Indexing** means turning a document into the tree of sections described in file 01, with a summary on every section. It happens once per document. Asking questions later only reads the result.

It is deliberately done in **two stages**:

```
Stage 1  build_index   (free, local)     file -> blocks -> tree -> split big sections -> save images
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

A `.docx` file is a zip of XML files. The `python-docx` library opens it. `doc.element.body.iterchildren()` walks the main body **in document order**: each child is a paragraph (`w:p`) or a table (`w:tbl`) (or other things we ignore). `qn("w:p")` converts the short prefix form into the full namespaced XML name that the library uses internally, so the comparison `element.tag == ...` works.

Reading in order matters: the order of blocks is the order of the document, and the tree builder depends on it.

**Paragraph branch**

```python
blocks.extend(_images_in(element, doc))
paragraph = DocxParagraph(element, doc)
text = paragraph.text.strip()
if not text:
    continue
level = _heading_level(paragraph)
blocks.append(Heading(level, text) if level else Paragraph(text))
```

1. Any images inside this paragraph are added first.
2. The paragraph is wrapped so its `.text` is readable. Blank paragraphs are skipped (an image-only paragraph still gave its image above).
3. If the paragraph's **style** is "Heading N", it becomes a `Heading`; otherwise a plain `Paragraph`.

```python
_HEADING_STYLE = re.compile(r"^heading (\d)$", re.IGNORECASE)

def _heading_level(paragraph):
    style = paragraph.style
    match = _HEADING_STYLE.match(style.name) if style is not None and style.name else None
    return int(match.group(1)) if match else None
```

This is the key to "Word has a ready-made structure": authors mark headings with the built-in *Heading 1 / 2 / 3* styles, and the style name is stored in the file. The regex `^heading (\d)$` accepts "Heading 1" in any capitalization and captures the digit as the level. Note the **Title** style (the big title line at the top of a document) does *not* match, so it becomes ordinary text. That is why, in the sample, the title ends up as the root's own text.

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

## Stage 1, step C: split big sections (`split.py`)

An agent asking to read a 40-page section would waste its whole budget. So any section with too much *own* text is cut into pieces.

```python
def estimate_tokens(text): return len(text) // 4
```

A **token** is the unit AI models read (roughly 3/4 of a word). Counting them exactly needs the model's own tokenizer; `len(text) // 4` is the rule of thumb "about 4 characters per token" for English. Good enough to decide "is this big?".

```python
def split_large_nodes(node, max_tokens):
    for child in node.children:
        split_large_nodes(child, max_tokens)       # handle the whole tree
    if estimate_tokens(node.text) <= max_tokens:
        return                                      # small enough, leave it
    chunks = _chunk_paragraphs(node.text, max_tokens)
    prefix = "" if node.id == "root" else f"{node.id}."
    generated = [Node(id=f"{prefix}p{i}", title=f"{node.title} (part {i} of {len(chunks)})",
                      level=node.level + 1, origin="generated", text=chunk,
                      location=node.location.model_copy(deep=True))
                 for i, chunk in enumerate(chunks, start=1)]
    node.text = ""
    node.children = [*generated, *node.children]
```

If a node's text is over the limit (`max_leaf_tokens`, default 2000 tokens, about 8,000 characters):

1. Cut the text into chunks (`_chunk_paragraphs`).
2. Make a **new child node per chunk**, id like `2.1.p1`, `2.1.p2`, marked `origin="generated"` and given a placeholder title "Roster (part 1 of 2)". Each child copies the parent's `Location` (`model_copy(deep=True)`) so citations still point to the real heading.
3. Empty the parent's own text and put the new children **before** its existing children (the text came before the subsections in the document).

The placeholder titles are replaced with real descriptive titles in stage 2 (the summarizer is told to rename only `generated` nodes).

```python
def _chunk_paragraphs(text, max_tokens):
    chunks, current, size = [], [], 0
    for paragraph in text.split("\n\n"):
        tokens = estimate_tokens(paragraph)
        if current and size + tokens > max_tokens:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks
```

Greedy packing: add paragraphs to the current chunk until the next one would overflow, then start a new chunk. Cuts only ever happen **between paragraphs**, never mid-sentence. An image placeholder travels with its paragraph, so an image ends up in the correct part.

Known limit: a single paragraph bigger than the limit stays whole (it is never cut inside).

---

## Stage 1, step D: put it together (`pipeline.build_index`)

```python
def build_index(path, store, settings) -> DocumentIndex:
    kind, blocks = _extract(path)
    doc_id = make_doc_id(path)
    root, raw_images = build_tree(blocks, title=path.stem, kind=kind)
    source_toc = [TocEntry(title=b.title, level=b.level) for b in blocks if isinstance(b, Heading)] or None
    split_large_nodes(root, settings.max_leaf_tokens)
    images = [ImageRecord(id=image_id,
                          file=store.save_image(doc_id, image_id, image.data, image.ext),
                          mime=_MIME_TYPES.get(image.ext, f"image/{image.ext}"),
                          description=image.alt)
              for image_id, image in raw_images]
    index = DocumentIndex(doc_id=doc_id, title=path.stem, source_path=path.name, kind=kind,
                          source_toc=source_toc, root=root, images=images)
    _assign_image_nodes(index)
    return index
```

Line by line:

1. `_extract` picks the reader by file extension. For `.pdf` it raises `NotImplementedError("PDF support is stage 3")`. This is the *right* use of that error: a feature genuinely not written yet. (The web page checks for PDFs earlier and shows a friendly message instead of letting this fire.)
2. `make_doc_id` (below) names the document.
3. Tree, then `source_toc` (the heading list, kept as reference; `or None` turns an empty list into `None`), then splitting.
4. Each raw image is **written to disk right now** via `store.save_image`, and the `ImageRecord` remembers the relative path. `description=image.alt` seeds the description with the file's own alt text if it has any.
5. `_assign_image_nodes` runs last, **after** splitting, so an image that moved into a generated part is linked to that part, not the parent:

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
def estimate_index(index, store) -> Estimate:
    sections = list(index.root.walk())
    input_tokens = 0
    for node in sections:
        text_tokens = estimate_tokens(node.text[:MAX_SECTION_CHARS])
        input_tokens += PROMPT_OVERHEAD_TOKENS + text_tokens + SUMMARY_TOKENS * len(node.children)
    images = sum(should_describe(record, (doc_dir / record.file).read_bytes())
                 for record in index.images if (doc_dir / record.file).exists())
    return Estimate(sections=len(sections), images=images, calls=len(sections) + images,
                    input_tokens=input_tokens + images * IMAGE_INPUT_TOKENS,
                    output_tokens=len(sections) * OUTPUT_TOKENS + images * IMAGE_OUTPUT_TOKENS)
```

It mirrors what stage 2 will do. **One Claude call per section** plus **one per describable image**. For each section the input is: a fixed instruction overhead (about 250 tokens) + the section's own text (cut at 12,000 characters like the real prompt) + about 70 tokens for each child's summary (a parent's prompt includes its children's summaries). Images are charged a flat guess of 1,500 input tokens. The number constants at the top (`250`, `70`, `110`, `1500`, `80`) are **estimates, not measurements**; that is why the page says "roughly". `Estimate` is a frozen dataclass (immutable result record).

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
def enrich_index(index, store, settings, api_key=None, on_progress=None):
    from ..llm import chat_model
    from .summarize import describe_images, summarize_tree

    total = estimate_index(index, store).calls
    done = 0
    lock = threading.Lock()

    def on_step(label):
        nonlocal done
        with lock:
            done += 1
            if on_progress:
                on_progress(done, total, label)

    describe_images(index.images, store.doc_dir(index.doc_id), chat_model(settings.vision_model, api_key), settings.index_workers, on_step)
    summarize_tree(index.root, chat_model(settings.index_model, api_key), index.images, settings.index_workers, on_step)
    index.summary = index.root.summary
```

- The imports are **inside the function** on purpose: the tests replace `pageindex_rag.llm.chat_model` with a fake and that only works if it is looked up at call time; and it avoids loading the Anthropic libraries when no AI call is being made.
- `on_step` is called from the worker threads, possibly at the same moment, so the counter is protected by a **lock** (`with lock:` lets one thread at a time through). `nonlocal done` lets the inner function change the outer variable.
- Images are described **first**, then summaries, because the summaries use the image descriptions.
- The vision model and the summary model come from settings (both Haiku by default); the visitor's `api_key` is passed to both. Finally the document's `summary` is a copy of the root's.

### `index_document`: the two stages in one call

```python
def index_document(path, store, settings, use_llm=True, api_key=None, on_progress=None):
    index = build_index(path, store, settings)
    if use_llm:
        enrich_index(index, store, settings, api_key, on_progress)
    store.save_index(index)
    return index
```
Used by the command line, the sample builder and the tests. The web page calls the two stages separately so it can show the cost in between. `use_llm=False` skips stage 2 completely (no summaries): many tests use this.

---

## Things that are not built, or limited

1. **A Word file with no Heading styles gets no real structure.** All its text lands under the root and is only cut by size into "(part n of m)" sections. The design intends to have Claude *generate* a table of contents for such files; **that is not implemented yet**. It was planned together with PDF.
2. **PDF reading does not exist** (`NotImplementedError`, see above).
3. Word features that are ignored: headers and footers, footnotes, text boxes, nested tables. Merged table cells may repeat their text.
4. The token estimate is a rough rule (characters / 4).
5. One enormous paragraph is never split.
6. If any one Claude call fails (a bad key, a rate limit), the **whole indexing run stops** and the half-built document is deleted. There is no "resume". (The Anthropic library already retries transient errors a couple of times by itself.)
7. Six parallel calls could exceed a small account's rate limit; the number is configurable (`PAGEINDEX_INDEX_WORKERS`).
8. How *good* Haiku's summaries are on real documents has not been tested; that is the next thing to look at with your real key.
