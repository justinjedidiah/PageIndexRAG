# 05. The web app (Reflex)

Files covered: everything in `pageindex_app/` (`runtime.py`, `viewmodels.py`, `files.py`, `state.py`, `views.py`, `pageindex_app.py`), plus `rxconfig.py` and `assets/app.css`.

Everything so far (files 01-04) is plain Python with no web in it. This layer puts a website on top. The web app contains **no document logic of its own**: it calls the functions from the earlier files and shows the results. (That is deliberate: the core package never imports anything from `pageindex_app`, which is why it can be tested and used from the command line without a browser.)

---

## First, how Reflex works

**Reflex** is a framework for writing a whole website in Python. You do not write HTML or JavaScript. Instead:

1. **The page** is built from Python functions that return components (`rx.vstack(...)`, `rx.button(...)`). Reflex compiles them into a React web page (JavaScript) that runs in the visitor's browser.
2. **The state** is a Python class (`State`) living **on the server**. Its fields are the data the page shows. Each connected visitor gets their own `State` object.
3. **Event handlers** are methods on the state. A click, a form submit or a page load in the browser sends a message to the server over a **websocket** (a permanent two-way connection). The matching Python method runs **on the server**, changes some fields, and Reflex sends only the *changes* back; the browser re-draws.

So when you press "Ask", your browser does not run any of our code. It tells the server "the ask event happened", our Python runs, and the answer streams back as state changes.

Important consequences for this project:
- **Fields that are visible to the page** are sent to the browser. Fields whose names **start with an underscore** (`_api_key`) are *backend variables*: they stay on the server and are never sent. This is how the visitor's API key stays out of the browser.
- A normal handler holds a lock on the visitor's state while it runs, so the page can't change under it. A handler marked `background=True` doesn't hold the lock: it is for long tasks (indexing, the agent) and must use `async with self:` whenever it touches state.
- In the page code you cannot use normal Python `if`/`for` on state values, because at build time they are not values yet, only *references to variables*. Reflex provides `rx.cond` (if), `rx.foreach` (loop) and operators like `~State.key_set` that produce *instructions* for the browser to evaluate.

---

## `runtime.py`: shared objects

```python
settings = get_settings()
sessions = SessionManager(settings.sessions_dir, settings.session_ttl_seconds)
```

Created **once** when the app starts and imported wherever needed (state, routes, the sweep). One shared `SessionManager` means all parts agree on the same session folder and lifetime.

## `viewmodels.py`: data shaped for the page

```python
@dataclasses.dataclass
class ChatMessage:
    role: str
    text: str = ""
    steps: list[str] = dataclasses.field(default_factory=list)
    citations: list[CitationView] = dataclasses.field(default_factory=list)
    images: list[ImageView] = dataclasses.field(default_factory=list)
    found: bool = True
    error: bool = False
```

Small dataclasses (`DocView`, `CitationView`, `ImageView`, `ChatMessage`, `OutlineRowView`) with **plain typed fields**. The page needs to loop over lists and read each item's fields; Reflex can do that when the items are dataclasses with declared types. They are *view* models: only what the screen needs, not the full core objects (a `Citation` from the agent becomes a `CitationView`).

A `ChatMessage` is one finished bubble: a user message (just `text`), or a finished answer with its `steps` (the navigation path), `text`, `citations` and `images`. The answer still being written is not a `ChatMessage` yet: it lives in `live_steps` and `live_text` (see `ask` below). `found=False` marks a reply with no citations (shown in grey, still rendered as Markdown). `error=True` marks text the app wrote rather than the model (a failed call, an empty library, a refusal notice, a used-up step budget); only those are left out of the history sent back to the agent. Real replies are kept, cited or not, so follow-ups like "tell me more about p3" have their context.

## `files.py`: serving images

The page needs to show document images. Browsers fetch images by URL, so the server needs a route for it. Reflex lets you add your own routes next to its own:

```python
async def serve_image(request: Request) -> Response:
    scope = request.path_params["scope"]
    try:
        library = Library.for_session(settings, sessions, None if scope == SAMPLE_SCOPE else scope)
    except ValueError:
        return Response(status_code=404)
    path = library.image_file(request.path_params["doc_id"], f"images/{request.path_params['name']}")
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type=image_media_type(path), headers=HEADERS)

api = Starlette(routes=[Route("/api/files/{scope}/{doc_id}/images/{name}", serve_image)])
```

A URL like `/api/files/sample/meridian-...-e3546aaf/images/img-1.png` is split into pieces (`scope`, `doc_id`, `name`) by the route pattern. `scope` is `"sample"` (shared library) or the visitor's session token. The function builds the right `Library` and asks it for the file; **all the safety checks are in `Library.image_file`** (file 04): letters-only ids, image extensions only, and the resolved path must be inside the document's folder. A bad session token (which would raise `ValueError`) is also a plain 404, and every failure looks identical (no hints for an attacker).

`HEADERS`: `Cache-Control: private, max-age=3600` (the browser may reuse the image for an hour but shared caches must not) and `X-Content-Type-Options: nosniff` (the browser must treat it as an image, never guess it is something else).

Starlette is the small web framework Reflex itself is built on, so this plugs in naturally.

## `pageindex_app.py`: the app itself

```python
app = rx.App(api_transformer=api, stylesheets=["/app.css"])
app.add_page(index, route="/", title="PageIndex RAG", on_load=State.on_load)
```

- `api_transformer=api` merges our image route into the server. Reflex puts its own app *inside* ours, so our routes are checked first.
- `stylesheets=["/app.css"]` loads `assets/app.css` (the `assets` folder is served as-is).
- `add_page` registers the single page: the function `index` (in `views.py`) draws it at `/`; `on_load=State.on_load` runs that handler **every time the page is opened**, to load the document list.

```python
async def sweep_expired_sessions() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(sessions.sweep)
            if removed:
                log.info("Removed %d expired session(s)", removed)
        except Exception:
            log.exception("Session sweep failed")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)

app.register_lifespan_task(sweep_expired_sessions)
```

A function that runs **for as long as the server is alive**: delete expired sessions, then sleep 300 seconds, forever. `register_lifespan_task` tells Reflex to start it with the server and stop it with the server. `asyncio.to_thread(sessions.sweep)` runs the (slow, file-deleting) sweep on a helper thread so it can never block the website. The `try/except` means a failed sweep is logged and the loop carries on, instead of dying silently.

## `rxconfig.py` and `assets/app.css`

```python
config = rx.Config(
    app_name="pageindex_app",
    plugins=[rx.plugins.RadixThemesPlugin(theme=rx.theme(appearance="light", accent_color="gray", gray_color="sand", radius="large"))],
    disable_plugins=[rx.plugins.SitemapPlugin],
)
```
`app_name` tells Reflex which package holds the app. The theme (set in your other session) is the overall look: light mode, gray accent, "sand" tint, large rounded corners. `SitemapPlugin` (an automatic sitemap feature) is switched off because it is not wanted and prints a warning.

`app.css` loads the Inter font from Google Fonts and sets page background and quieter scrollbars. One thing to be aware of: **the visitor's browser downloads that font from Google**, so Google can see that request. It carries no documents or keys, but if the privacy story matters, the font can be self-hosted instead.

---

## `state.py`: all the behaviour

This is the biggest file. Every button on the page ends in one of its handlers.

### Module helpers

```python
def image_url(scope, doc_id, relative):
    return f"{rx.config.get_config().api_url}/api/files/{scope}/{doc_id}/{relative}"
```
Builds the full URL of an image. The page (served by Reflex's front end on one port) and the image route (on the back end port) are different addresses in development, so the back end's address (`api_url`) is included.

`pending_path(token)` = the visitor's `pending.json` file (explained under `handle_upload`). `scroll_chat(force=False)` returns a small piece of JavaScript (`rx.call_script`) that scrolls the chat so the newest text is in view; it targets the invisible `chat-end` element at the bottom of the chat. It only scrolls if the visitor is already within 250 px of the bottom, so someone who scrolled up to re-read an earlier answer is not pulled down while a new one streams. `force=True` (used when they send a question) scrolls anyway.

### The variables

```python
class State(rx.State):
    _api_key: str = ""           # backend only, never sent to the browser
    key_set: bool = False
    key_error: str = ""
    key_checking: bool = False

    docs: list[DocView] = []
    has_samples: bool = False
    session_note: str = ""

    messages: list[ChatMessage] = []
    thinking: bool = False

    upload_error: str = ""
    building: bool = False
    _pending_id: str = ""        # backend only
    _pending_file: str = ""      # backend only
    has_pending: bool = False
    pending_name: str = ""
    pending_detail: str = ""
    indexing: bool = False
    index_done: int = 0
    index_total: int = 0
    index_label: str = ""

    source_open / source_title / source_location / source_summary / source_text
    browse_open / browse_title / browse_rows
```

Read it as **"what the page can currently be showing"**. For example the page shows the indexing progress bar when `indexing` is `True`, the cost question when `has_pending` is `True`, and the normal upload form otherwise. Handlers just flip these fields and the page follows.

The comment on `_pending_id` records a real bug found in testing: this state originally held the whole unfinished `DocumentIndex` object, but Reflex wraps objects stored in state in a tracking proxy that worker threads must not modify. Now the index waits **on disk** (`pending.json`) and state holds only its id.

### Computed variables

```python
@rx.var
def can_chat(self) -> bool:
    return self.key_set and bool(self.docs) and not self.thinking

@rx.var
def index_percent(self) -> int:
    return int(100 * self.index_done / self.index_total) if self.index_total else 0

@rx.var
def own_doc_count(self) -> int:
    return sum(1 for doc in self.docs if not doc.sample)
```
`@rx.var` makes a field whose value is **calculated from other fields** whenever they change. `can_chat` is what enables or disables the input box (needs a key, at least one document, and not already answering). `index_percent` feeds the progress bar (guarded against dividing by zero). `own_doc_count` counts the visitor's own documents (to enforce the 5-document limit).

### Helper methods (names start with `_`)

Reflex turns every public method of a state class into an event handler; a leading underscore says "this is just a helper".

- `_token()`: the visitor's id (`self.router.session.client_token`).
- `_library()`: the visitor's `Library` (samples + their live session documents).
- `_refresh()`: re-reads the library into `self.docs` and rewrites the `session_note` text ("Your uploads are deleted automatically in 1 hour 52 minutes" if a session exists, else "...2 hours after you add the first one").
- `_clear_pending(delete_files)`: forgets the pending document; with `delete_files=True` it also deletes its images, upload and `pending.json` (used on cancel and on failure).
- `_finish_answer(generation, message)`: adds a finished answer to `messages` and clears the live answer and `thinking`, all in one update so the live bubble and the finished one never show together. It does nothing if the chat was cleared (End session bumps `_chat_generation`) while the answer was being written. Like every list change here, it **assigns** a new list (`self.messages = [*self.messages, message]`): Reflex notices changes when a field is assigned; changing something inside a list in place may not be noticed.

### The handlers, one by one

**`on_load`**: runs when the page opens. Calls `_refresh()`.

**`end_session`**: the "End session and delete my data" button. Deletes the visitor's whole session folder (`sessions.delete`), clears the key and chat and any pending upload, refreshes, and returns a confirmation toast. Everything the visitor added is gone, immediately.

If an "Index it" is running, `end_session` also **stops it**, so the visitor's key is not spent after they asked to leave. Each running job is an `IndexingJob` in the module-level `_jobs` dict (keyed by session token; it lives outside the state because Reflex state cannot hold a `threading.Event` or a lock). `end_session` sets the job's `cancel` event and deletes the folder while holding the job's `lock`. `enrich_index` checks `cancel` before every Claude call, so the calls already sent (at most `index_workers`) finish and nothing new starts. The job saves only while holding the same lock, after checking `cancel`, so it can never save into, and so recreate, the deleted folder. A cancelled job then leaves the page state alone: the visitor may already have started a new upload in the same tab. The toast says "Indexing was stopped." 

**`save_key(form_data)`**:
The key card has a **provider** dropdown (Anthropic, OpenAI, Google Gemini; `set_provider`, only while no key is set) above the key box. The dropdown sits outside the key's form on purpose: the form clears itself after each submit, so the key does not linger in the box, and a dropdown inside it would snap back to its first value, so an OpenAI key would be checked against Anthropic. The key box's hint and the "get a key" link follow the chosen provider (`key_placeholder`, `key_url`).

```python
key = str(form_data.get("api_key", "")).strip()
...
self.key_checking = True
yield
try:
    models = await asyncio.to_thread(list_models, self.provider, key)   # free: doubles as the key check
except Exception as exc:
    self.key_error = friendly_error(exc, info.label)
else:
    self._api_key, self.key_set = key, True
    self.models = models
    self.index_model = self.agent_model = ""     # nothing pre-selected: the visitor picks both
finally:
    self.key_checking = False
```
`form_data` is the submitted form as a dictionary (the input was named `api_key`). The bare `yield` **pushes the current changes to the page immediately** (so the "Use" button shows its spinner) before the slow part. Listing the models is a network call, so it runs on a helper thread (`asyncio.to_thread`) to avoid freezing the server. Outcomes: an error becomes a friendly message naming the provider (never the raw text); success stores the key in the backend variable, sets `key_set` and keeps the list of models this key can use. The card then shows "OpenAI key set" and two dropdowns filled from `models`, both starting at "Choose a model": **Model for indexing** and **Model for chat** (`set_index_model`, `set_agent_model`). Nothing is pre-selected. `can_chat` waits for a chat model, and `chat_hint` / `index_hint` say what is missing (a key, a chat model, an indexing model unless the Free preset is chosen). `_llm()` bundles provider, key and models into an `LLMConfig` for each indexing run and each answer. `try/except/else/finally`: `else` runs only if nothing was raised; `finally` always runs.

**`clear_key`**: forgets the key and its model list (`_forget_key`).

**`handle_upload(files)`**: the longest handler. In order:
1. Refuse if something is already in progress, or if no file was chosen. No key is needed to upload: reading the file and building the tree are free, and the Free preset never uses a key.
2. `name = safe_filename(upload.filename ...)` cleans the name. `data = await upload.read(settings.max_upload_bytes + 1)` reads the file **but never more than the limit plus one byte**, so an oversized upload cannot fill the server's memory; the `+1` lets the validator see "too big".
3. `validate_upload(...)` (file 04). On refusal it shows the message.
4. A PDF is refused for now ("PDF indexing is not available yet").
5. `doc_id_for(name, data)` works out the document's id from its name and a fingerprint of its content, **before anything is written**, so a refused upload never starts a session (or its 2-hour clock):
   - one of the **samples**: refused ("That's one of the sample documents, already in the library"); samples are read-only.
   - already in **your** library: allowed. Indexing it again (perhaps with another preset or model) **replaces** that version; the pending card says so (`replace_note`, below).
   - otherwise a new document, and the documents-per-session limit applies.
6. `self.building = True; yield` shows the spinner. Then in a `try`:
   - `sessions.ensure(token)` creates the session folder (this is the moment the 2-hour clock starts), saves the upload into `uploads/`.
   - `build_index` (stage 1, on a helper thread) with the options currently chosen (`self._options()`).
   - `finally: self.building = False` always turns the spinner off. Any unexpected error is logged **without the document's content** and shown as a generic message.
7. The unsummarized document is saved as `pending.json`; state remembers only its id and file name. `_show_estimates` fills `preset_choices` (one `PresetView` per preset: Free, Budget, Full, each with its own cost sentence from `estimate_index`) and `pending_detail` (the cost of the options chosen now, e.g. "12 sections, 1 image to describe. About 13 Claude calls on your key (roughly X input and Y output tokens)."). The page shows the three presets as clickable cards, an **Advanced options** toggle, that sentence, and **Index it** / **Cancel**. For a re-upload, `replace_note` adds "Already in your library (indexed with Budget · OpenAI gpt-6-luna). Indexing it again replaces that version.", read from the saved index's `options` and `indexed_with` (the provider and models that built it; never the key).

**Replacing safely.** A re-upload shares its folder with the version already in the library, and nothing is overwritten until the new run succeeds: `store.save_index` writes the new index only at the end (the images are the same, since the file is the same). Cancelling, or a failed run, goes through `_clear_pending(delete_files=True)`, which removes a document's folder **only if no index was ever saved there**: a brand-new upload's leftovers go, an existing document stays exactly as it was. This rule needs no extra state, so it also holds for an upload restored after a server restart.

**Indexing options.** The options live in state as plain values (`opt_split`, `opt_max_leaf`, `opt_small`, `opt_min_leaf`, `opt_summaries`, `opt_summary_length`, `opt_images`: strings and booleans, never an `IndexOptions` object, for the proxy reason above); `_options()` turns them into an `IndexOptions`. `preset` is the matching preset name, or `"custom"` once the advanced panel makes them match none.
- `set_preset(name)` copies the preset's LLM settings (summaries, length, images) into those fields and refreshes the estimates. The tree is not rebuilt: presets do not change it.
- Each advanced control has its own handler (`set_opt_split`, `set_opt_small`, ...). A **tree** option (split, max size, small sections, tiny size) rebuilds the pending tree with `build_index` on a helper thread (free, no LLM; `rebuilding` shows a spinner on Index it), then refreshes the estimates. A summary or image option only refreshes the estimates.
- **Confirmation before spending.** "Index it" calls `ask_to_index`: with options that use no model (Free) it starts at once; otherwise it opens a confirmation popup (`confirm_indexing_dialog`, a Radix alert dialog) showing the document's name, a small table of preset, model, document size, model calls and tokens. Only **Start indexing** (`start_indexing`) runs `confirm_index`; Cancel closes it and the document keeps waiting. `_show_estimates` fills the popup's parts (`pending_calls`, `pending_tokens`, `pending_size`).
- `needs_key_to_index` is true when the options use the model; then **Index it** is disabled until a key is set, with a hint to add one or pick Free.

**`cancel_pending`**: forget and delete the pending document.

**`confirm_index`** (a background handler, because it takes minutes):
```python
async with self:
    if not self._pending_id or self.indexing or self.rebuilding:
        return
    options = self._options()
    if options.uses_llm and not self.key_set:
        self.upload_error = "Add your API key first, or choose the Free preset (it needs no key)."
        return
    key, token = self._api_key, self._token()
    source = sessions.uploads_dir(token) / self._pending_file
    index = self._load_pending()
    total = estimate_index(index, sessions.store(token), options).calls
    self.indexing = True
    ...
```

The work itself runs in one helper-thread function, `run(index)`: if the pending tree was built with different options (for example the visitor switched between previews and summaries after uploading), it is rebuilt from the uploaded file first; then `enrich_index` runs only if the options use Claude; then the index is saved. With the Free preset there are no calls, so it finishes almost at once.
`async with self:` is how a background handler gets (briefly) safe access to state: read what it needs, set `indexing`, leave. It loads the pending document back from `pending.json`. Then, **outside** the lock (so the page stays responsive and can show progress):

```python
progress = {"done": 0, "total": total, "label": "Starting"}
def on_progress(done, total, label):         # called from the worker threads
    progress.update(done=done, total=total, label=label)

work = asyncio.create_task(asyncio.to_thread(enrich_index, index, store, settings, key, on_progress))
try:
    while not work.done():
        await asyncio.wait({work}, timeout=0.4)
        async with self:
            self.index_done, self.index_total, self.index_label = progress["done"], progress["total"], progress["label"]
    work.result()
    await asyncio.to_thread(store.save_index, index)
except Exception as exc:
    error = friendly_error(exc)
```
The real indexing (stage 2 from file 02) runs on a helper thread as a task. The worker threads write their progress into a plain dictionary; meanwhile this handler **wakes every 0.4 seconds**, copies those numbers into state (which pushes them to the progress bar) and goes back to sleep, until the task is done. `work.result()` re-raises any error from the thread. On success the finished index is saved. After the loop (success or failure) it takes the lock once more: on failure it shows `Indexing failed. <friendly message>` and **deletes the half-built document**; on success it deletes `pending.json`, refreshes the library, and shows a green toast.

**`delete_doc(doc_id)`**: removes one of the visitor's own documents. Samples cannot be deleted (`library.is_sample`), checked on the server even though the page hides the button.

**`open_source(ref)`**: a citation chip or an outline row was clicked. `ref` is `"doc_id:node_id"`; it is split on the first colon, the document and node are loaded, and the source panel's fields are filled: title, location line ("Document · Crew and Roles > Roster"), the section's **summary** and its text rendered by `node_markdown` (images become real URLs). If the section no longer exists (the session expired) it shows an error toast instead.

**`open_browse(doc_id)`**: fills the outline panel using `outline_rows`; each row's indent is `depth * 1.2` em.

`set_source_open` / `set_browse_open`: small setters used by the close buttons.

**`ask_example(question)`** returns `State.ask({"question": question})`: it chains into the normal `ask` handler as if the visitor had typed it.

**`ask(form_data)`**: the heart of the chat. A background handler.

**Why the answer being written is kept apart.** Reflex sends a changed variable to the browser in full. If the streaming answer were the last item of `messages`, every update (about ten a second) would re-send the whole conversation, and that cost grows with every question asked. Measured with the offline model: question 1 received 12.8 KB while it streamed, question 15 received 209.5 KB. So the answer being written lives in two small variables, `live_steps` and `live_text`, shown by `live_answer()` below the finished messages while `thinking` is true, and joins `messages` once, when it is done. With that, question 15 receives 38.4 KB; what is left is the two times per question the message list itself changes (the question added, the answer added), not a cost per streamed word.

```python
async with self:
    if self.thinking or not self.key_set:
        return
    key, token = self._api_key, self._token()
    history = [ChatTurn(role=m.role, content=m.text) for m in self.messages
               if m.text and (m.role == "user" or not m.error)][-MAX_HISTORY_TURNS * 2 :]
    generation = self._chat_generation
    self.thinking = True
    self.live_steps, self.live_text = [], ""
    self.messages = [*self.messages, ChatMessage(role="user", text=question)]
yield scroll_chat(force=True)
```
Inside the lock: refuse if already answering or no key; build the **history** to give the agent (earlier finished messages with text, excluding app-written error replies; only the last 12 messages = 6 question/answer pairs); remember which chat this answer belongs to (`generation`); mark `thinking` (the page shows the live answer, "Working..." until text arrives); add the visitor's bubble; scroll.

```python
events = stream_ask(indexes, question, chat_settings, history, api_key=key)
while (event := await asyncio.to_thread(next, events, None)) is not None:
    if isinstance(event, Step):
        steps.append(StepView(event.label, event.note))
        async with self:
            if self._chat_generation != generation:
                return                       # the chat was cleared meanwhile
            self.live_steps = list(steps)
            self.live_text = ""
    elif isinstance(event, Draft):
        if time.monotonic() - last_draft < DRAFT_INTERVAL:
            continue
        last_draft = time.monotonic()
        async with self:
            ...
            self.live_text = event.text
    else:
        message = ChatMessage(role="assistant", steps=list(steps), **_answer_fields(event, library, token))
        async with self:
            self._finish_answer(generation, message)
    yield scroll_chat()
```
Outside the lock it loads the documents and starts the agent's event stream (file 03). The agent code makes slow network calls **inside** `next(events)`, so each `next` is run on a helper thread (`asyncio.to_thread(next, events, None)`; the `None` is what `next` returns when the stream ends). Each event is a `Step` (add a `StepView(label, note)` to the live answer: this is how the navigation path grows live, with the model's one-line note above its step; any draft text is cleared because the step now carries it), a `Draft` (the answer text so far, so the answer types out), or the final `AnswerResult` (turned into a finished `ChatMessage` with the steps, text, citations and images, and added to the chat by `_finish_answer`). Drafts can arrive many times a second, so any that come less than `DRAFT_INTERVAL` (0.08 s) after the last one are skipped; nothing is lost because the next draft or the final answer contains the full text. Each update is followed by a (non-forced) scroll. `:=` is the walrus operator (assign and test in the same expression).

```python
except EmptyLibrary: ...  _finish_answer(generation, ChatMessage(..., "There are no documents to search yet.", error=True))
except Exception as exc:  ...  _finish_answer(generation, ChatMessage(..., friendly_error(exc), error=True))
finally:
    async with self:
        if self._chat_generation == generation and self.thinking:
            self._finish_answer(generation, None)
```
Errors become an assistant message flagged `error` (a custom `EmptyLibrary` exception is used instead of a built-in one so unrelated errors can't be mistaken for it). `finally` guarantees `thinking` is switched off and the live answer cleared **no matter what happened**, so the input box can never get stuck disabled, unless the chat was cleared meanwhile, in which case End session already did that.

**`_answer_fields(result, library, token)`** turns the agent's `AnswerResult` into the fields of the bubble: text, found flag, citations as `CitationView` (the click target `ref` = `doc_id:node_id`) and images as `ImageView` with their URLs (each image's scope is `sample` or the visitor's token).

---

## `views.py`: the page

This is the version redesigned in your other session: a three-column workspace. It is plain "functions that return components", each small, composed from the bottom up.

### Reading component code

```python
rx.vstack(
    rx.text("Library", size="2", weight="bold"),
    rx.cond(State.docs.length() > 0, rx.vstack(rx.foreach(State.docs, doc_row)), rx.text("No documents yet.")),
    spacing="2", align="start", width="100%",
)
```
- `rx.vstack` / `rx.hstack` / `rx.box` / `rx.grid`: layout containers (vertical stack, horizontal stack, plain box, grid). Their keyword arguments are styling (`spacing`, `width`, `padding`...).
- `rx.cond(condition, if_true, if_false)`: show one thing or the other, decided in the browser from state.
- `rx.foreach(State.docs, doc_row)`: draw `doc_row` once per item of the list `State.docs`.
- `on_click=State.open_browse(doc.doc_id)`: "when clicked, send the server the `open_browse` event with this document's id".
- Style constants at the top (`BORDER`, `SURFACE`, `CARD`, `MUTED`, `LABEL`, `PRIMARY`) are dictionaries unpacked with `**CARD` so every card looks the same.

### `prose`: markdown in its own component

```python
@rx.memo
def prose(text: rx.Var[str]) -> rx.Component:
    return rx.box(rx.markdown(text), style=PROSE_STYLE)
```
Renders text as Markdown (answers, section text) with table borders and images that fit (`PROSE_STYLE`). It is a **memo component**: it becomes its own React component. This was needed because Reflex's markdown element calls a React "hook" while it is built, and hooks may only be called from inside a component, never from a loop or condition. Putting markdown directly inside the message list made React complain ("change in the order of Hooks") whenever the first message appeared; wrapping it fixed the warning. It is called with a keyword (`prose(text=...)`): memo components accept only keyword arguments.

### Left column (`left_column`)

- `key_card`: a form (`rx.form`) whose submit sends the `save_key` event; a password-type input; shows a green "Key set" badge and a Remove button once a key is set; shows `State.key_error` in red; and the privacy sentence ("stays in this session's server memory only...").
- `library_card`: lists `State.docs` through `doc_row` (title, a "sample" badge, a Browse button, and a trash button only for non-samples: `rx.cond(~doc.sample, ...)`), plus the `session_note`.
- `upload_card`: a three-way switch on state: `indexing` -> `indexing_view` (progress bar and "N of M: label"); else `has_pending` -> `pending_view` (the cost sentence with **Index it** / **Cancel**); else `upload_form`. The upload form is `rx.upload`: a drop zone limited to the accepted types, one file and the size limit, open without a key. Its `on_drop` sends `handle_upload` as soon as a file is chosen or dropped, so there is no separate Upload button; while the file is read it shows a spinner and refuses another file. A file of the wrong type or size never reaches the server: the drop zone rejects it and Reflex shows a toast.
- `agent_settings_card`: collapsed by default (`show_agent_settings`, toggled by clicking its title row). Holds the "Outline levels per look" select (`listing_depth`, 1-3), passed to `stream_agent` with each question: how many outline levels one agent listing shows.

### Middle column (`chat_panel`)

- A scrolling area containing: `empty_chat()` (instructions, and the four sample-question cards) **only while there are no messages**; the messages (`rx.foreach(State.messages, message_view)`); and an invisible box with `id="chat-end"` that the scroll script targets.
- `message_view`: `rx.cond(message.role == "user", <bubble on the right>, assistant_message(message))`.
- `live_answer` is the answer being written, shown under the messages while `thinking`: its steps so far, then a spinner and "Working..." until text arrives, then the text as it streams.
- `assistant_message` (a finished answer) stacks, in order: the steps (`rx.foreach(message.steps, step_line)`, only when there are any): each is the model's one-line note at the same size as the answer, then a small grey line saying what it did, so the message reads like a conversation; the answer (`prose`, the same look whether or not it was found); the images; and the **citation chips** (each one `on_click=State.open_source(citation.ref)`).
- `composer`: a form with the text input named `question`, sending `State.ask` on submit and clearing itself (`reset_on_submit`). The input and the arrow button are disabled when `can_chat` is false.

### Right column (`source_panel`)

```python
rx.cond(State.source_open, source_view(), rx.cond(State.browse_open, browse_view(), placeholder))
```
A single panel that shows **the source viewer** (title, location, the summary in a callout, the section text via `prose`), or **the outline** (every section of a document, indented, each clickable), or a placeholder ("Click a citation, or Browse a document, to read it here"). Clicking a section in the outline calls `open_source`, so it flips to the source view.

### Putting it together and responsive layout (`index`)

```python
rx.grid(
    left_column(), chat_panel(), source_panel(),
    columns=rx.breakpoints(initial="1", lg="300px minmax(0, 1fr) 380px"),
    ...
)
```
`rx.breakpoints` gives a different value per screen size: **one column** on phones and small screens (everything stacks), **three columns** (300 px | flexible | 380 px) on large screens. Other props use lists for the same purpose, e.g. `height=["80vh", "80vh", "100%"]` (a list is small, medium, large). The whole workspace is one rounded bordered panel capped at 1500 px wide with a header above it (`header()`: logo, title, and the End-session button).

---

## Known limits of the web layer

1. **State lives in the server's memory.** If the server restarts, every visitor's key and chat are lost (their uploaded documents on disk remain until the sweep). Fine for a demo; a real deployment would need to decide how to handle restarts.
2. **No rate limiting.** One visitor could upload up to 5 documents of 15 MB each repeatedly, using the server's CPU and disk (they pay Anthropic, but the server cost is yours). A per-address limit is not built.
3. **The key is typed into the page and travels to the server once.** It is not stored or logged, but on a public site this needs **HTTPS**, otherwise it crosses the network readable. (Reflex's default memory state is used; if you ever configure Redis for state, the key would be stored there too.)
4. **Chat history is not saved anywhere**: closing the tab loses it.
5. **PDF uploads** are accepted by the drop zone but refused with a message (file 02).
6. **Never run against a real key from the web page yet**: the browser test (file 06) uses the offline fake model.
7. The page loads a font from Google (see above).
