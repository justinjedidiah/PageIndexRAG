# 04. Supporting modules: settings, keys, sessions, uploads, library

Files covered: `config.py`, `providers.py`, `llm.py`, `errors.py`, `sessions.py`, `uploads.py`, `library.py`, `outline.py`, `cli.py` (all in `src/pageindex_rag/`).

These are the small modules that the indexing code (file 02), the agent (file 03) and the web app (file 05) all lean on. Several of them exist mainly for **security and privacy**, which matters because the finished app is public and handles other people's API keys and documents.

---

## `config.py`: every setting in one place

```python
load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
```

- `load_dotenv()` reads the project's `.env` file and copies each `NAME=value` line into the process's **environment variables**. That is how your `ANTHROPIC_API_KEY` reaches the program without being in the code.
- `PROJECT_ROOT`: `__file__` is the path of `config.py`; `.resolve()` makes it absolute; `.parents[2]` goes up from `src/pageindex_rag/` -> `src/` -> the project folder. It lets the default for the samples folder work no matter where the program is started from.

```python
@dataclass(frozen=True)
class Settings:
    data_dir: Path
    index_model: str
    ...
    session_ttl_seconds: int = 2 * 60 * 60
    max_upload_bytes: int = 15 * 1024 * 1024
    max_session_docs: int = 5
    index_workers: int = 6
    samples_dir: Path = PROJECT_ROOT / "samples" / "index"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"
```

A **frozen dataclass**: a bundle of settings that cannot be changed after creation (a safety feature: no part of the program can quietly alter a setting). Fields without defaults come first, then those with defaults (Python's rule). `@property` makes `settings.sessions_dir` look like a field but compute its value.

```python
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("PAGEINDEX_DATA_DIR", "data")),
        provider=provider,                       # PAGEINDEX_PROVIDER, default "anthropic"
        index_model=os.getenv("PAGEINDEX_INDEX_MODEL", ""),   # no default models: the CLI must name them
        ...
```

`os.getenv(NAME, default)` reads an environment variable or uses the default. Numbers are converted with `int(...)`. The full list, all optional:

| Environment variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | none | Only for the command line and sample building. **Never** set on a public server. |
| `PAGEINDEX_PROVIDER` | `anthropic` | Provider for the CLI: `anthropic`, `openai` or `google`. The web app uses each visitor's choice. |
| `PAGEINDEX_DATA_DIR` | `data` | Where files are stored. Relative to the folder you run from. |
| `PAGEINDEX_INDEX_MODEL` | none (required by the CLI to index with an LLM) | Writes section summaries |
| `PAGEINDEX_VISION_MODEL` | the index model | Describes images |
| `PAGEINDEX_AGENT_MODEL` | none (required by `pageindex ask`) | Navigates and answers; must support tool calling |
| `PAGEINDEX_MAX_LEAF_TOKENS` | 2000 | Sections bigger than this get split |
| `PAGEINDEX_AGENT_MAX_STEPS` | 15 | Agent step budget |
| `PAGEINDEX_SESSION_TTL_SECONDS` | 7200 | Session lifetime (2 hours) |
| `PAGEINDEX_MAX_UPLOAD_MB` | 15 | Upload size cap |
| `PAGEINDEX_MAX_SESSION_DOCS` | 5 | Documents per visitor |
| `PAGEINDEX_INDEX_WORKERS` | 6 | Parallel model calls while indexing |
| `PAGEINDEX_SAMPLES_DIR` | `samples/index` | The shared sample library |
| `PAGEINDEX_LLM_FACTORY` | none | Test hook: swap in the fake model (file 06) |

The web app calls `get_settings()` once at start-up (`pageindex_app/runtime.py`) and shares that object.

---

## `providers.py`: Anthropic, OpenAI and Google Gemini

Everything that differs between model providers lives in this one file; the rest of the code talks to LangChain's common interface (tool calls, structured output, image input), which all three support.

```python
PROVIDERS = {
    "anthropic": ProviderInfo("anthropic", "Anthropic", "sk-ant-...", <key page>, "ANTHROPIC_API_KEY", small_caps=True),
    "openai": ProviderInfo("openai", "OpenAI", "sk-...", <key page>, "OPENAI_API_KEY"),
    "google": ProviderInfo("google", "Google Gemini", "AIza...", <key page>, "GOOGLE_API_KEY"),
}
```

A `ProviderInfo` per provider: its name, what its keys look like (the key box's hint), where to get a key, and the environment variable LangChain falls back to. It names **no models**: model lineups change every few months, so the visitor picks both models from the list their key can use (see `list_models`), and the dev CLI reads them from `.env`.

```python
@dataclass(frozen=True)
class LLMConfig:
    provider: Provider = "anthropic"
    api_key: str | None = field(default=None, repr=False)
    index_model: str = ""      # empty until chosen
    vision_model: str = ""     # empty -> the index model
    agent_model: str = ""      # empty until chosen
```

One visitor's (or the CLI's) choice, passed to indexing (`enrich_index(..., llm=...)`) and chat (`stream_ask(..., llm_config=...)`). `repr=False` on the key means printing a config, or logging one by mistake, never shows the key.

**`chat_model(config, model, max_tokens)`** is the only place a model is created (an empty model name raises `NoModelChosen`, with a message saying how to choose one): `ChatAnthropic`, `ChatOpenAI` or `ChatGoogleGenerativeAI`. The test hook `PAGEINDEX_LLM_FACTORY` (for example `tests.fake_llm:make_model`) swaps in the offline model instead. The key is passed only when given; with none, LangChain falls back to the provider's environment variable, which is why it must never be set on a public server: the app must only ever spend *the visitor's* key.

**Output caps.** Summaries are capped at 256 or 512 output tokens on Anthropic. OpenAI and Gemini models can spend output tokens on hidden reasoning before answering, so a tight cap could leave no room for the answer; they get a generous cap (`LLMConfig.output_cap`). The prompt sets the summary length either way.

**`list_models(provider, api_key)`** asks the provider which models this key can use (`models.list` in each SDK) and keeps the chat models (drops speech, image, embedding and similar ones by name). Two reasons: model lineups change every few months, so the UI's dropdowns are filled from this live list instead of a list in the code; and listing models is **free**, so it doubles as the key check: a bad key raises the provider's own error.

---

## `llm.py`: the thin wrapper

```python
def chat_model(model, api_key=None, max_tokens=2048, provider="anthropic") -> BaseChatModel:
    return providers.chat_model(LLMConfig(provider, api_key), model, max_tokens)

def llm_config(settings, api_key=None) -> LLMConfig:
    return LLMConfig(settings.provider, api_key, index_model=settings.index_model, ...)
```

Indexing and the agent call `llm.chat_model(...)` (tests replace this one function with a fake), and `llm_config` builds an `LLMConfig` from settings for the CLI and for callers that pass no config.

---

## `errors.py`: turning API errors into safe messages

```python
def friendly_error(exc: Exception, provider: str | None = None) -> str:
    name = provider or _provider_name(exc)          # "OpenAI", from where the error came from
    status = _status(exc)                           # status_code (Anthropic, OpenAI) or code (Google)
    if _any(exc, lc.ModelAuthenticationError) or status == 401:
        return f"{name} rejected that API key. Check it and try again."
    ...
    return "Something went wrong. Try again."
```

It works for all three providers by checking, in order: LangChain's **standard error types** (`ModelAuthenticationError`, `ModelRateLimitError`, ...: the Gemini package raises these), then an **HTTP status** on the error (the Anthropic and OpenAI SDKs' own errors carry `status_code`, Google's carry `code`), then anything that looks like a **connection** or timeout failure. It looks through the errors an exception was raised from (`__cause__`), since LangChain often wraps the SDK's error. Each case maps to a fixed sentence: bad key (401), not allowed (403), model not available for this key (404), rate limit or no credit (429), cannot reach the provider, other status, anything else.

A **400 ("bad request")** has many causes, so it is not given one blanket meaning. The provider's own error text usually says which, so `_bad_request` reads it (on the server, lower-cased, for matching only) and looks for known causes: tools or functions "not supported" (the chosen chat model cannot do tool calling), `response_format` / `json_schema` not supported (the indexing model cannot do structured output), an image that is not supported (the indexing model cannot read images), or a context-length overflow (too long for this model). Each gets its own fixed sentence that tells the visitor what to change; anything else becomes "{Provider} couldn't process the request with this model (status 400). Choosing another model may help." The raw text is still never shown or logged.

The docstring states the reason: **never include the raw exception text.** Raw errors can contain details of the request. Showing only our own fixed sentences means a visitor's key can never leak into the page or logs through an error message. (Unit tests put a fake key into each provider's raw errors and check it never appears in the output.)

---

## `sessions.py`: per-visitor storage that deletes itself

The problem: visitors upload private documents to a public server. You do not want them kept. Without accounts there is no "log out", so storage needs a **time limit**.

```python
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_MARKER = "session.json"
```

Each visitor has a **client token** (a random id Reflex gives their browser). It is used as the visitor's **folder name**. A folder name made from outside input is dangerous: a malicious token like `../../something` could point outside the sessions folder (a "path traversal" attack). So every token must match the regular expression: only letters, digits, `_` and `-`, 8 to 128 characters. Anything else raises an error.

```python
@dataclass(frozen=True)
class Session:
    token: str
    created: float
    expires_at: float
    was_reset: bool = False
```
A small immutable record of a live session. Times are **Unix timestamps** (seconds since 1970, `time.time()`).

```python
class SessionManager:
    def __init__(self, root, ttl_seconds, clock=time.time):
        self.root = Path(root); self.ttl = ttl_seconds; self.clock = clock
```
`clock` is a function that returns "now". It defaults to `time.time` but can be replaced: the tests pass a fake clock they control, so "two hours later" is one line of code instead of a two-hour wait. Passing something in instead of hard-coding it is called **dependency injection**.

```python
def session_dir(self, token):
    if not _TOKEN.match(token):
        raise ValueError("Invalid session token.")
    return self.root / token
```
The one door every path goes through, with the validation.

```python
def get(self, token):
    created = self._created(self.session_dir(token))
    if created is None or self.clock() - created >= self.ttl:
        return None
    return Session(token, created, created + self.ttl)
```
"Is there a live session?" Read when it was created; if there is no marker, or **age >= lifetime**, there is no session.

```python
def ensure(self, token):
    directory = self.session_dir(token)
    session = self.get(token)
    if session:
        return session
    was_reset = directory.exists()
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    now = self.clock()
    (directory / _MARKER).write_text(json.dumps({"created": now}), encoding="utf-8")
    return Session(token, now, now + self.ttl, was_reset)
```
"Give me a live session, creating one if needed." If one is live, return it unchanged. Otherwise **wipe any stale folder** (an expired session's leftover files), create a fresh folder and write a small `session.json` marker with the creation time. `was_reset` reports whether something stale had to be cleared.

Two design decisions worth knowing:
- **Lifetime counts from creation, not from last use.** Two hours is a hard cap: simple, predictable, and good for privacy. (A visitor still chatting at 1:59 loses everything at 2:00.)
- **The creation time lives in a marker file** rather than the folder's own modification time, because a folder's modified time changes every time a file is added.
- The folder is created on the **first upload**, not when the page loads, so someone who only browses the samples never gets a folder.

```python
def sweep(self):
    if not self.root.is_dir(): return 0
    removed = 0
    now = self.clock()
    for directory in self.root.iterdir():
        if not directory.is_dir(): continue
        created = self._created(directory)
        if created is None or now - created >= self.ttl:
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    return removed
```
**This is the function that guarantees cleanup.** It looks at every folder in the sessions directory and deletes those that are expired, **or have no valid marker** (unknown junk counts as expired). Stray *files* (not folders) are ignored. The web app runs it every 5 minutes in the background (file 05), so data can live up to about 2 hours 5 minutes.

```python
@staticmethod
def _created(directory):
    try:
        return float(json.loads((directory / _MARKER).read_text(encoding="utf-8"))["created"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
```
Reads the marker. Any problem (missing file, bad JSON, missing key) is treated as `None` = "no valid session". The tuple after `except` lists the several error types it handles.

Why the sweep, and not "delete when the visitor leaves"? The web framework offers no reliable "visitor left" event: a dropped Wi-Fi connection or a page reload looks the same as leaving, and deleting someone's documents on a reload would be wrong. So the time-based sweep is the guarantee, and the **End session** button is the early exit (`delete`).

`describe_duration(seconds)` turns `3900` into `"1 hour 5 minutes"` for the "deleted automatically in ..." note on the page. `divmod(minutes, 60)` returns the whole hours and the leftover minutes in one step.

---

## `uploads.py`: refusing bad files

Rule from the design: **strictly `.pdf` and `.docx`**. The file's *name* is chosen by the visitor and can lie, so the code checks the *contents* too.

```python
class UploadError(ValueError):
    """The message is safe to show to the visitor."""
```
A custom error type. Raising it means "refuse this file and here is a message that is safe to display".

```python
def safe_filename(name):
    base = PurePath(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:100] or "upload"
```
Makes a filename safe to store and show. Steps: change Windows `\` to `/`, take only the **last part** (`PurePath(...).name`), so `../../etc/passwd` becomes `passwd` and `C:\Users\me\file.docx` becomes `file.docx`; replace every character outside letters, digits, `.`, `_`, space and `-` with `_`; trim spaces and dots from the ends; cut to 100 characters; and if nothing is left use `"upload"`.

```python
def validate_upload(filename, data, max_bytes, max_uncompressed=MAX_UNCOMPRESSED_BYTES) -> FileKind:
    suffix = PurePath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".zip":
        raise UploadError("ZIP archives are not accepted. Upload a .pdf or .docx file.")
    if suffix not in ALLOWED:
        raise UploadError("Only .pdf and .docx files are accepted.")
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > max_bytes:
        raise UploadError(...)
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadError("That file is not a valid PDF.")
        return "pdf"
    _check_docx(data, max_uncompressed)
    return "docx"
```

Checks run **cheapest first**: the extension (a dedicated friendly message for `.zip`), then empty, then size, then the real contents. A PDF must begin with the bytes `%PDF-` (every real PDF does). A Word file goes through `_check_docx`.

**Why `.docx` needs a special check.** A `.docx` is secretly a **zip archive** of XML files, so "no zip files" can't mean "reject anything zip-shaped". Instead: reject files *named* `.zip`; and for `.docx`, make sure the zip really is a Word document and is not hostile.

```python
def _check_docx(data, max_uncompressed):
    not_docx = UploadError("That file is not a valid Word (.docx) document.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise not_docx from None
    with archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise not_docx
        if len(entries) > MAX_ZIP_ENTRIES:
            raise UploadError("That document contains too many parts.")
        if sum(entry.file_size for entry in entries) > max_uncompressed:
            raise UploadError("That document is too large once unpacked.")
        for entry in entries:
            if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
                raise UploadError("That document looks like a compression bomb and was refused.")
```

1. Open the bytes as a zip **in memory** (`BytesIO`, never written to disk yet). Not a zip -> refused.
2. A genuine Word file always contains the parts `[Content_Types].xml` and `word/document.xml`. A renamed Excel file, text file or random zip lacks them -> refused.
3. **Zip-bomb defense.** A "zip bomb" is a tiny file that expands to gigabytes (for example 100 MB of zeros compresses to a few kilobytes). We read the sizes *declared in the archive's directory* (no unpacking needed) and refuse if: there are more than 2,000 parts, the total unpacked size exceeds 100 MB, or any single part expands by more than **200 times** (a normal image or XML file is nowhere near that).

Limits: the PDF check only looks at the first bytes, so a deliberately malformed PDF would still pass this stage and reach whatever reads PDFs later; that PDF reader (not written yet) will need to be careful too.

---

## `library.py`: what one visitor sees

A visitor's library = the **shared sample documents** (read-only, same for everyone) + **their own uploaded documents** (private, expiring).

```python
class Library:
    def __init__(self, samples: Store | None, session: Store | None):
```
Holds two `Store`s (file 01). Either can be `None`.

```python
@classmethod
def for_session(cls, settings, sessions, token):
    live = sessions.get(token) if token else None
    return cls(Store(settings.samples_dir), sessions.store(token) if live else None)
```
Builds a library for one visitor. The session store is included **only if their session is live**, so an expired session silently shows just the samples. `@classmethod` means it is called on the class (`Library.for_session(...)`), and `cls(...)` creates an instance.

```python
def documents(self):
    docs = {}
    for store, sample in ((self.session, False), (self.samples, True)):   # samples win on a clash
        for entry in store.list_documents() if store else []:
            docs[entry.doc_id] = DocInfo(entry.doc_id, entry.title, entry.kind, entry.summary, sample)
    return sorted(docs.values(), key=lambda d: (not d.sample, d.title.lower()))
```
Merges both lists into one dictionary keyed by `doc_id`. Session documents are added first and samples second, so on an id clash the sample entry replaces it. The sort key `(not d.sample, title)` puts **samples first** (`not True` = `False`, which sorts before `True`), then alphabetically by title. `store.list_documents() if store else []` skips a missing store.

```python
def load(self, doc_id):      # checks samples first, then the session
def indexes(self):           # loads every document: this is what the agent receives
def is_sample(self, doc_id)  # True if it is in the shared set (those cannot be deleted)
```

### `image_file`: the careful one

The browser asks the server for an image by URL; the server must turn a URL into a file on disk **without letting anyone read other files**.

```python
def image_file(self, doc_id, relative):
    if (not _DOC_ID.match(doc_id) or not _IMAGE_NAME.match(Path(relative).name)
            or Path(relative).parent.name != "images"):
        return None
    for store in (self.samples, self.session):
        if store and store.has_index(doc_id):
            base = store.doc_dir(doc_id).resolve()
            path = (base / "images" / Path(relative).name).resolve()
            return path if path.is_file() and path.is_relative_to(base) else None
    return None
```

Defense in layers:
1. `doc_id` must match `^[a-z0-9][a-z0-9-]{0,100}$` (no dots, slashes, uppercase...).
2. The file name must match `^[A-Za-z0-9_-]{1,64}\.(png|jpg|jpeg|gif|webp)$`, an image extension only, so `index.json`, text files and so on are impossible.
3. The path must be exactly `images/<name>`; `images/../index.json` has a parent named `..` and fails.
4. The document must exist in this visitor's library.
5. `.resolve()` turns the path into its true absolute form (collapsing any `..` or links) and `is_relative_to(base)` confirms it is genuinely **inside** the document's folder. Even if layers 1-3 had a hole, this stops escaping the folder.

Any failure returns `None`, which the web route turns into a plain "404 not found" (no hints about why). These exact cases are tested, both in unit tests and in the real running server.

`image_media_type` maps `.png` -> `image/png` etc. so the browser treats the response as an image.

---

## `outline.py`: display helpers for the web page

```python
def outline_rows(index) -> list[OutlineRow]:
    rows = []
    def visit(node, depth):
        for child in node.children:
            rows.append(OutlineRow(f"{index.doc_id}:{child.id}", child.title, child.summary, depth))
            visit(child, depth + 1)
    visit(index.root, 0)
    return rows
```
Flattens the tree into a list of rows (title, summary, depth) in reading order, for the **Browse** dialog (the page indents by `depth`). **Recursion**: `visit` calls itself for each child, adding 1 to the depth. The `ref` is in the same `doc_id:node_id` form the agent cites, so clicking a row and clicking a citation open the same viewer.

```python
def node_markdown(index, node, image_url) -> str:
    records = {record.id: record for record in index.images}

    def image(match):
        record = records.get(match.group(1))
        if record is None: return ""
        alt = re.sub(r"[\[\]\s]+", " ", record.description).strip()[:200] or "image"
        return f"![{alt}]({image_url(record)})"

    text = IMAGE_REF.sub(image, node.text)
    if not text.strip() and node.children:
        items = "\n".join(f"- **{child.title}**: {child.summary or 'no summary'}" for child in node.children)
        return f"_This section is divided into subsections:_\n\n{items}"
    return text
```
Produces the text shown in the **source viewer**. `IMAGE_REF.sub(image, text)` runs the function `image` on every `[image:img-1]` marker and substitutes whatever it returns: here a Markdown image `![description](url)`. The description is cleaned (brackets and line breaks collapsed to spaces, 200 character limit) so odd text can't break the Markdown. A marker for an unknown image vanishes. A section with **no text of its own** (just subsections) shows its subsections as a list instead of an empty box.

The URL is built by a function the caller passes in (`image_url`), so this module knows nothing about web addresses, which keeps it testable.

---

## `cli.py`: the developer command line

Installed as the command `pageindex` (declared under `[project.scripts]` in `pyproject.toml`). It is **not part of the product**: it is a developer tool to index and test without the web page.

```python
parser = argparse.ArgumentParser(prog="pageindex")
commands = parser.add_subparsers(dest="command", required=True)
index_cmd = commands.add_parser("index", ...)
index_cmd.add_argument("path")
index_cmd.add_argument("--no-llm", action="store_true", ...)
```
`argparse` is Python's built-in command-line parser. Sub-commands are `index <file> [--no-llm]`, `list`, `tree <doc_id>` and `ask "<question>" [--doc <id>]`. `action="store_true"` makes `--no-llm` a flag that is `True` when present.

The `index` branch calls `index_document`; `list` prints the library; `tree` prints a document's tree with `_print_tree` (recursion again; a `*` marks generated nodes and the text length helps spot huge ones); `ask` loads the documents and iterates `stream_ask`: it prints each step as `  > Opened "..."`, then the answer, citations and images.

Imports like `from .indexing import index_document` sit **inside** the `if` branches so a quick `pageindex list` does not load the heavy AI libraries.

The CLI uses the provider in `PAGEINDEX_PROVIDER` and its key in your `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GOOGLE_API_KEY`), unlike the web app, which only uses the visitor's.
