from toc_rag.indexing.blocks import Heading, Image, Paragraph
from toc_rag.indexing.options import matching_preset, preset_options
from toc_rag.indexing.preview import fill_previews
from toc_rag.indexing.refine import refine_tree
from toc_rag.indexing.tree import build_tree
from toc_rag.models import IndexOptions


def words(n: int, word: str = "word") -> str:
    return " ".join([word] * n)  # about 1.5 tokens per word ("word " is 6 characters)


PREAMBLE = Paragraph(words(40))  # root text, so the root does not absorb a lone top heading


def options(**overrides) -> IndexOptions:
    return IndexOptions(**{"max_leaf_tokens": 200, "min_leaf_tokens": 20, **overrides})


def shape(node) -> list:
    return [(child.id, child.title, shape(child)) for child in node.children]


def good_tree():
    blocks = [
        Heading(1, "Intro"),
        Paragraph(words(60)),
        Heading(1, "Method"),
        Paragraph(words(40)),
        Heading(2, "Setup"),
        Paragraph(words(60)),
        Heading(2, "Run"),
        Paragraph(words(60)),
    ]
    return build_tree(blocks, "Doc", "docx")[0]


def test_a_good_tree_comes_back_unchanged():
    for mode in ("off", "merge", "fold"):
        root = good_tree()
        before = root.model_dump()
        refine_tree(root, options(small_sections=mode, max_leaf_tokens=100))  # nothing small enough to fold
        assert root.model_dump() == before, mode


def test_off_changes_nothing_even_with_tiny_sections():
    blocks = [Heading(1, "Only"), Heading(2, "A"), Paragraph("a"), Heading(2, "B"), Paragraph("b")]
    root = build_tree(blocks, "Doc", "docx")[0]
    before = root.model_dump()
    refine_tree(root, options(small_sections="off"))
    assert root.model_dump() == before


def test_merge_joins_runs_of_tiny_leaves_and_keeps_their_titles():
    blocks = [
        PREAMBLE,
        Heading(1, "Parts"),
        Paragraph(words(40)),
        Heading(2, "Bolt"),
        Paragraph("M6 steel."),
        Heading(2, "Nut"),
        Paragraph("M6 nylon."),
        Heading(2, "Frame"),
        Paragraph(words(60)),  # not tiny: breaks the run
        Heading(2, "Washer"),
        Paragraph("Flat."),  # a run of one is left alone
    ]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())

    parts = root.children[0]
    assert [c.title for c in parts.children] == ["Bolt · Nut", "Frame", "Washer"]
    merged = parts.children[0]
    assert merged.id == "1.1"  # the first merged section's id
    assert merged.origin == "generated"
    assert merged.text == "Bolt\nM6 steel.\n\nNut\nM6 nylon."
    assert merged.location.covers == ["Bolt", "Nut"]
    assert merged.location.label() == "Parts > Bolt, Nut"


def test_merge_never_joins_sections_with_subsections_or_beyond_the_limit():
    blocks = [
        PREAMBLE,
        Heading(1, "Top"),
        Paragraph(words(40)),
        Heading(2, "A"),
        Paragraph(words(10)),
        Heading(2, "B"),
        Heading(3, "B1"),
        Paragraph(words(40)),
        Heading(3, "B2"),
        Paragraph(words(40)),
        Heading(2, "C"),
        Paragraph(words(10)),
    ]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())
    assert [c.title for c in root.children[0].children] == ["A", "B", "C"]  # A and C are not adjacent

    tiny = [block for i in range(6) for block in (Heading(2, f"S{i}"), Paragraph(words(10)))]
    root = build_tree([PREAMBLE, Heading(1, "Top"), Paragraph(words(40)), *tiny], "Doc", "docx")[0]
    refine_tree(root, options(max_leaf_tokens=50))
    merged = root.children[0].children
    assert len(merged) > 1  # six ~15-token sections cannot all fit one 50-token read
    assert all(len(node.text) // 4 <= 50 for node in merged)


def test_collapse_absorbs_single_child_chains():
    blocks = [
        Heading(1, "Manual"),  # the document's only top heading
        Heading(2, "Safety"),
        Heading(3, "Rules"),  # chain: Safety > Rules
        Paragraph(words(60)),
        Heading(2, "Use"),
        Paragraph(words(60)),
    ]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())

    assert root.title == "Doc" and root.id == "root"  # the root keeps the document's title
    assert [c.title for c in root.children] == ["Safety › Rules", "Use"]
    safety = root.children[0]
    assert safety.id == "1.1" and safety.children == []
    assert safety.location.heading_path == ["Manual", "Safety", "Rules"]


def test_a_section_with_real_text_is_not_collapsed():
    blocks = [PREAMBLE, Heading(1, "A"), Paragraph(words(60)), Heading(2, "B"), Paragraph(words(60))]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())
    assert [c.title for c in root.children] == ["A"]
    assert [c.title for c in root.children[0].children] == ["B"]


def test_fold_turns_small_subtrees_into_one_leaf():
    blocks = [
        Heading(1, "Big"),
        Paragraph(words(150)),
        Heading(1, "Crew"),
        Paragraph("Who is on board."),
        Heading(2, "Captain"),
        Paragraph("Ada."),
        Image(b"x", "png"),
        Heading(3, "Deputy"),
        Paragraph("Grace."),
    ]
    root, _ = build_tree(blocks, "Doc", "docx")
    refine_tree(root, options(small_sections="fold"))

    crew = next(c for c in root.children if c.title == "Crew")
    assert crew.children == []
    assert crew.text == "Who is on board.\n\nCaptain\nAda.\n\n[image:img-1]\n\nDeputy\nGrace."
    assert crew.location.covers == ["Captain", "Deputy"]
    assert crew.location.label() == "Crew > Captain, Deputy"
    assert root.children  # the root is never folded


def test_merge_mode_does_not_fold_bigger_subtrees():
    blocks = [PREAMBLE, Heading(1, "Crew"), Paragraph(words(40)), Heading(2, "Captain"), Paragraph(words(40))]
    blocks += [Heading(2, "Deputy"), Paragraph(words(40))]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())
    assert [c.title for c in root.children[0].children] == ["Captain", "Deputy"]


def test_a_document_without_headings_becomes_one_level_of_parts():
    paragraphs = [f"Topic {i} begins here. " + words(80) for i in range(10)]
    root = build_tree([Paragraph(p) for p in paragraphs], "Notes", "docx")[0]
    refine_tree(root, options())

    assert root.text == ""
    assert len(root.children) > 1
    assert all(not child.children for child in root.children)  # one level, no grouping
    assert root.children[0].title == "Notes: Topic 0 begins here."
    assert root.children[0].id == "p1"


def test_split_can_be_turned_off():
    root = build_tree([PREAMBLE, Heading(1, "Long"), Paragraph(words(800))], "Doc", "docx")[0]
    refine_tree(root, options(split_large=False))
    assert root.children[0].children == []


def test_previews_use_the_first_sentences_and_list_subsections():
    blocks = [
        Heading(1, "Engines"),
        Paragraph("Two engines. Both diesel. Each has 400 kW. Serviced yearly."),
        Heading(2, "Port"),
        Paragraph("The port engine."),
        Heading(2, "Starboard"),
        Image(b"x", "png"),
        Paragraph("The starboard engine."),
    ]
    root = build_tree(blocks, "Doc", "docx")[0]
    fill_previews(root, sentences=2)

    engines = root.children[0]
    assert engines.summary == "Two engines. Both diesel. Covers: Port, Starboard."
    assert engines.children[1].summary == "The starboard engine."  # image placeholders are left out
    assert root.summary == "Covers: Engines."
    assert all(node.summary_source == "preview" for node in root.walk())


def test_presets_differ_only_in_llm_settings(settings):
    free, budget, full = (preset_options(name, settings) for name in ("free", "budget", "full"))
    assert not free.uses_llm and budget.uses_llm and full.uses_llm
    assert (free.summaries, budget.summary_length, full.summary_length) == ("preview", "short", "detailed")
    assert not budget.describe_images and full.describe_images
    assert {o.max_leaf_tokens for o in (free, budget, full)} == {settings.max_leaf_tokens}
    assert [matching_preset(o) for o in (free, budget, full)] == ["free", "budget", "full"]
    assert matching_preset(full.model_copy(update={"describe_images": False, "summary_length": "detailed"})) is None


def test_a_section_left_with_one_merged_child_absorbs_it():
    blocks = [
        PREAMBLE,
        Heading(1, "Overview"),
        Heading(2, "Mission"),
        Paragraph("Run experiments."),
        Heading(2, "Layout"),
        Paragraph("Four modules."),
        Heading(1, "Big"),
        Paragraph(words(60)),
    ]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())

    overview = root.children[0]
    assert overview.title == "Overview" and overview.children == []
    assert overview.text == "Mission\nRun experiments.\n\nLayout\nFour modules."
    assert overview.location.label() == "Overview > Mission, Layout"
    assert overview.origin == "original"


def test_the_root_keeps_an_outline_when_its_only_child_is_a_leaf():
    blocks = [Paragraph("Guide"), Heading(1, "Feeding"), Paragraph("Fish."), Heading(1, "Habitat"), Paragraph("Pool.")]
    root = build_tree(blocks, "Doc", "docx")[0]
    refine_tree(root, options())
    assert [c.title for c in root.children] == ["Feeding · Habitat"]  # merged, but not swallowed by the root
    assert root.text == "Guide"
