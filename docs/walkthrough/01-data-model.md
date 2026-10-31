# 01. The data model and storage

Files covered: `src/pageindex_rag/models.py`, `src/pageindex_rag/indexing/blocks.py`, `src/pageindex_rag/storage.py`.

Start here. Every other part of the project either **builds** these shapes (indexing), **reads** them (the agent, the UI) or **saves** them (storage). If you understand these three files, the rest is just "who fills in which field, and when".

---

## Python concepts used in this file

**Pydantic `BaseModel`.** A normal Python class stores whatever you put in it. A pydantic model is a class with *typed fields* that (a) checks the types when you create it, (b) fills in defaults, and (c) converts itself to and from JSON in one call (`model_dump_json()` and `model_validate_json()`). That last point is why the project saves its data with almost no code.

**`Field(default_factory=list)`.** A default like `= []` on a class would be one list *shared by every instance*. `default_factory=list` says "call `list()` fresh for each new object" so every `Node` gets its own empty `children` list.

**`Literal["pdf", "docx", "markdown"]`.** A type that only allows exactly those three strings. Anything else fails validation.

**`from __future__ import annotations`.** Lets a class mention itself in its own field types. `Node` has `children: list[Node]`, which would otherwise fail because `Node` does not exist yet while Python is still reading the class body.

---

## `models.py`

### `FileKind`

```python
FileKind = Literal["pdf", "docx", "markdown"]
```

The three source types. `markdown` is only used by the developer command line and the tests (the web page refuses it). `pdf` is in the list already because the data model is ready for PDF even though the PDF reader is not written yet.

### `Location`: where a section came from

```python
class Location(BaseModel):
    kind: FileKind
    heading_path: list[str] = Field(default_factory=list)
    pdf_page_start: int | None = None   # 0-based index into the PDF
    pdf_page_end: int | None = None
    printed_page_start: str | None = None  # page label as printed on the page
    printed_page_end: str | None = None
```

A `Location` is **only used for citations**: the text shown to the visitor after an answer ("Crew and Roles > Roster"). It is deliberately kept separate from the section's text, so the text can stay plain and the "where is this?" information can differ per file type.

- For Word files there are no stable pages (Word re-flows text depending on the program), so the location is the **heading path**: the list of headings from the top down to this section.
- For PDFs, the page fields are reserved. Two separate numbers are kept on purpose: the PDF's own page index (0, 1, 2...) and the page number *printed* on the page (which might be "iv" or "212" even if it is the 230th page). Visitors should be shown the printed one.

```python
    def label(self) -> str:
        if self.kind == "pdf" and self.printed_page_start:
            if self.printed_page_end and self.printed_page_end != self.printed_page_start:
                return f"pp. {self.printed_page_start}-{self.printed_page_end}"
            return f"p. {self.printed_page_start}"
        return " > ".join(self.heading_path) or "(document start)"
```

`label()` makes the human-readable string. For a PDF with page info it returns `p. 7` or `pp. 7-9`. Otherwise it joins the heading path with ` > `, giving `Life Support > Oxygen supply`. If there is no path (text before the first heading) it says `(document start)`.

### `Node`: one section of a document

```python
class Node(BaseModel):
    id: str
    title: str
    level: int
    origin: Literal["original", "generated"] = "original"
    summary: str = ""
    text: str = ""  # content directly under this node, before its first child
    location: Location
    children: list[Node] = Field(default_factory=list)
```

The most important class in the project. A document is a **tree of `Node`s**.

| Field | Meaning |
|---|---|
| `id` | Position in the tree: `"1"`, `"1.2"`, `"1.2.1"`. The root is the special id `"root"`. Generated sub-parts use `"1.p1"`, `"1.p2"` (see `split.py`). The agent uses these ids to ask for sections. |
| `title` | The heading text, e.g. `Roster`. |
| `level` | Heading depth: 1 for Heading 1, 2 for Heading 2. The root is 0. Used only while building the tree. |
| `origin` | `"original"` if the heading existed in the file; `"generated"` if our own size-splitting created it. |
| `summary` | The 2-4 sentence description Claude writes at index time. **This is what the agent reads to decide where to go.** Empty until indexing's second stage. |
| `text` | The section's **own** text: what sits under its heading *before the first subheading*. Text of subsections lives in the subsections, never duplicated in the parent. |
| `location` | Citation info, described above. |
| `children` | The subsections, in document order. |

```python
    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            yield from child.walk()
```

`walk()` visits this node and then every descendant, depth-first. It is a **generator**: `yield` hands back one node and pauses, so you can write `for node in root.walk():` without building a giant list. `yield from child.walk()` means "hand out everything that child's own walk hands out". It is used everywhere (estimating, summarizing, mapping ids).

A worked example, the sample handbook, becomes this tree:

```
root  "meridian-station-operations-handbook"      text: "Meridian Station Operations Handbook" (the Word title line)
├─ 1   "Overview"                                  text: ""
│   ├─ 1.1  "Mission"                              text: "Meridian Station is a fictional ..."
│   └─ 1.2  "Station layout"                       text: "The station has four modules ...\n\n| Module | Purpose | ..."
├─ 2   "Crew and Roles"
│   ├─ 2.1  "Roster"
│   └─ 2.2  "Shift schedule"
├─ 3   "Life Support"
│   ├─ 3.1  "Oxygen supply"                        text: "Oxygen is produced ...\n\n[image:img-1]\n\nFigure 1. ..."
...
```

Note `1 Overview` has no text of its own (no paragraph sits directly under it), only subsections. That matters later: the agent reading a node with empty text is told "its content is in its subsections".

### `ImageRecord`

```python
class ImageRecord(BaseModel):
    id: str                       # "img-1"
    file: str                     # path relative to the document's data folder, e.g. "images/img-1.png"
    mime: str                     # "image/png"
    node_id: str | None = None    # which section the image appears in
    location: Location | None = None
    description: str = ""         # written by the vision model (or the document's own alt text)
    tags: list[str] = Field(default_factory=list)
```

The image bytes are saved as files on disk; this record is the searchable description of one. The agent never sees pixels: it reads `description` (that is how "show me the oxygen chart" works). `node_id` and `location` are filled in after sectioning (`_assign_image_nodes` in `pipeline.py`).

### `TocEntry`

```python
class TocEntry(BaseModel):
    title: str
    level: int
    page: str | None = None
```

The document's *own* table of contents, kept **as reference data only**. The navigable tree is our refined one (with big sections split further). Today the entry list is just the headings found in the file; `page` is reserved for PDFs.

### `DocumentIndex`: one whole indexed document

```python
class DocumentIndex(BaseModel):
    doc_id: str
    title: str
    source_path: str   # the original file name only, so saved indexes are safe to share
    kind: FileKind
    summary: str = ""
    source_toc: list[TocEntry] | None = None
    root: Node
    images: list[ImageRecord] = Field(default_factory=list)

    def node_map(self) -> dict[str, Node]:
        return {node.id: node for node in self.root.walk()}

    def parent_map(self) -> dict[str, str]:
        return {child.id: node.id for node in self.root.walk() for child in node.children}
```

This is what gets written to `index.json`. `summary` is the root node's summary, repeated here so the library list can show a one-paragraph description without opening the whole tree.

The two helper methods are **dictionary comprehensions**:

- `node_map()` turns the tree into `{"1.1": <Node Mission>, "2.1": <Node Roster>, ...}` so "give me node 2.1" is an instant lookup instead of searching the tree.
- `parent_map()` gives `{"1.1": "1", "1": "root", ...}`. The agent's `go_up` tool uses it to find a section's parent. (The double `for` reads: for every node, for every child of it, record child -> parent.)

### `LibraryEntry`

```python
class LibraryEntry(BaseModel):
    doc_id: str
    title: str
    kind: FileKind
    summary: str = ""
```

A slim row used in lists (the library panel, the agent's "documents" listing). It lets the app show all documents without loading every tree.

---

## `indexing/blocks.py`: the flat middle step

```python
@dataclass
class Heading:   level: int; title: str
class Paragraph: text: str
class Table:     markdown: str
class Image:     data: bytes; ext: str; alt: str = ""

Block = Heading | Paragraph | Table | Image
```

(Written out in the file as four small `@dataclass` classes.) A **dataclass** is the lightweight cousin of a pydantic model: it just stores fields. No validation is needed here because these never leave the program.

**Why this layer exists.** Reading a `.docx`, a Markdown file and (later) a PDF are three very different jobs. Building a tree from headings is one job. If each reader produced a tree directly, you would write the tree logic three times. Instead every reader produces the same **flat list of blocks in document order**, and one function (`build_tree`) turns any such list into a tree. Adding PDF later means writing only a "PDF to blocks" reader.

`Block = Heading | Paragraph | Table | Image` is a **union type**: "a Block is one of these four".

```python
IMAGE_REF = re.compile(r"\[image:(img-\d+)\]")

def image_placeholder(image_id: str) -> str:
    return f"[image:{image_id}]"
```

Images are not text, but a section's text needs to remember *where* an image sat. So when the tree is built, an image is replaced by the marker `[image:img-1]` inside the text. `IMAGE_REF` is a **regular expression** that finds those markers: `\[image:` the literal characters, `(img-\d+)` a capture group of `img-` plus digits, `\]` the closing bracket. Three later features use it:
- the summarizer swaps each marker for the image's description,
- the source viewer swaps each marker for a real `<img>`,
- the pipeline uses it to find which node holds which image.

---

## `storage.py`: files on disk

There is **no database**. Everything is JSON files in folders, handled by one small class.

```
<data_dir>/
  library.json                  list of LibraryEntry (one per document)
  docs/
    <doc_id>/
      index.json                the full DocumentIndex
      images/
        img-1.png ...
```

```python
_library_adapter = TypeAdapter(list[LibraryEntry])
```

A pydantic model can validate *itself*, but `list[LibraryEntry]` is not a model, it is a plain list type. `TypeAdapter` is pydantic's way to say "give me the same JSON-to-objects conversion for this type too".

### `Store` methods

```python
def doc_dir(self, doc_id): return self.data_dir / "docs" / doc_id
```
`/` on a `Path` joins path pieces. Everything below builds on this.

```python
def save_image(self, doc_id, image_id, data, ext) -> str:
    relative = f"images/{image_id}.{ext}"
    path = self.doc_dir(doc_id) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative
```
Writes one image file, creating folders as needed (`parents=True` creates missing parents, `exist_ok=True` is not an error if already there). It **returns the relative path** (`images/img-1.png`), which is what gets stored in the `ImageRecord`. Storing it relative means the whole document folder can be moved or copied (as with the samples) and still work.

```python
def save_index(self, index):
    path = self.doc_dir(index.doc_id) / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json(indent=2), encoding="utf-8")

    entries = {entry.doc_id: entry for entry in self.list_documents()}
    entries[index.doc_id] = LibraryEntry(doc_id=index.doc_id, title=index.title,
                                         kind=index.kind, summary=index.summary)
    self.library_path.write_bytes(_library_adapter.dump_json(list(entries.values()), indent=2))
```
Two writes. First the full index. Then the library list: it **reads the existing list into a dictionary keyed by `doc_id`**, sets this document's entry (replacing any old one: re-indexing the same document overwrites instead of duplicating), and writes the list back.

```python
def delete_document(self, doc_id):
    shutil.rmtree(self.doc_dir(doc_id), ignore_errors=True)
    entries = [entry for entry in self.list_documents() if entry.doc_id != doc_id]
    if self.library_path.exists() or entries:
        self.library_path.write_bytes(_library_adapter.dump_json(entries, indent=2))
```
Deletes the folder (`ignore_errors=True` so a missing folder is fine) and rewrites the list without that document. The `if` avoids creating an empty `library.json` out of nothing.

`has_index` checks that `index.json` exists (a half-built document, with images but no index, does not count). `load_index` reads and validates the JSON into a `DocumentIndex`; `list_documents` returns `[]` if there is no `library.json` yet.

### Honest limits of this design

- **No locking and no atomic writes.** If two processes wrote the same `library.json` at once, one update could be lost. It is safe *today* because each visitor has their own `Store` (their own folder) and the web app blocks starting a second indexing while one is running; the shared sample store is read-only.
- **Everything loads whole.** `load_index` reads the entire document. Fine for the document sizes allowed (15 MB uploads); a huge library would want a database.
- Corrupt or hand-edited JSON raises an error at load time rather than being repaired.
