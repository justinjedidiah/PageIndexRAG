from pageindex_rag.indexing.blocks import Heading, Image, Paragraph, Table
from pageindex_rag.indexing.split import split_large_nodes
from pageindex_rag.indexing.tree import build_tree


def test_nests_blocks_under_headings():
    blocks = [
        Paragraph("Preface text"),
        Heading(1, "Intro"),
        Paragraph("Intro text"),
        Heading(2, "Background"),
        Table("| a | b |\n| --- | --- |\n| 1 | 2 |"),
        Heading(1, "Method"),
        Heading(3, "Deep detail"),  # skipped level still nests under Method
        Paragraph("Detail text"),
    ]
    root, images = build_tree(blocks, "Doc", "markdown")

    assert root.text == "Preface text"
    assert [c.id for c in root.children] == ["1", "2"]
    intro, method = root.children
    assert intro.text == "Intro text"
    assert intro.children[0].id == "1.1"
    assert intro.children[0].text.startswith("| a | b |")
    assert intro.children[0].location.heading_path == ["Intro", "Background"]
    assert method.children[0].id == "2.1"
    assert method.children[0].text == "Detail text"
    assert images == []


def test_images_become_placeholders():
    root, images = build_tree([Heading(1, "Crew"), Image(b"x", "png", "the crew")], "Doc", "markdown")
    assert [image_id for image_id, _ in images] == ["img-1"]
    assert root.children[0].text == "[image:img-1]"


def test_split_large_leaf_into_generated_parts():
    paragraphs = [f"Paragraph {i} " + "word " * 30 for i in range(6)]
    root, _ = build_tree([Heading(1, "Long"), *[Paragraph(p) for p in paragraphs]], "Doc", "markdown")

    split_large_nodes(root, max_tokens=50)

    long = root.children[0]
    assert long.text == ""
    assert len(long.children) > 1
    assert all(child.origin == "generated" for child in long.children)
    assert long.children[0].id == "1.p1"
    assert long.children[0].location.heading_path == ["Long"]
    rejoined = "\n\n".join(child.text for child in long.children)
    assert rejoined == "\n\n".join(paragraphs)


def test_small_nodes_are_not_split():
    root, _ = build_tree([Heading(1, "Short"), Paragraph("tiny")], "Doc", "markdown")
    split_large_nodes(root, max_tokens=50)
    assert root.children[0].children == []


def test_split_parts_are_balanced():
    from pageindex_rag.indexing.split import estimate_tokens, split_text

    paragraphs = ["x" * 160] * 5  # 40 tokens each, 200 in all, limit 150: two parts, not 150 + 50
    chunks = split_text("\n\n".join(paragraphs), 150)
    assert len(chunks) == 2
    sizes = [estimate_tokens(chunk) for chunk in chunks]
    assert max(sizes) <= 150 and min(sizes) >= 40


def test_a_giant_paragraph_is_split_on_sentences():
    from pageindex_rag.indexing.split import split_text

    sentences = [f"Sentence number {i} says something useful." for i in range(40)]
    chunks = split_text(" ".join(sentences), 100)
    assert len(chunks) > 1
    assert all(chunk.endswith(".") for chunk in chunks)  # never cut mid-sentence
    assert " ".join(chunks) == " ".join(sentences)


def test_a_big_table_is_split_by_rows_with_the_header_repeated():
    from pageindex_rag.indexing.split import split_text

    header = "| Part | Count |\n| --- | --- |"
    rows = [f"| part-{i:03d} | {i} |" for i in range(60)]
    chunks = split_text("\n".join([header, *rows]), 80)
    assert len(chunks) > 1
    for chunk in chunks:
        lines = chunk.split("\n")
        assert "\n".join(lines[:2]) == header
        assert all(line.startswith("| part-") for line in lines[2:])
    assert [row for chunk in chunks for row in chunk.split("\n")[2:]] == rows


def test_an_image_stays_with_its_paragraph():
    from pageindex_rag.indexing.split import split_text

    text = "\n\n".join(["a" * 160, "b" * 160, "[image:img-1]", "c" * 160, "d" * 160])
    chunks = split_text(text, 90)
    holder = next(chunk for chunk in chunks if "[image:img-1]" in chunk)
    assert holder != "[image:img-1]"
    assert "c" * 160 in holder


def test_a_word_longer_than_the_limit_is_hard_cut():
    from pageindex_rag.indexing.split import split_text

    chunks = split_text("z" * 1000, 50)
    assert "".join(chunks) == "z" * 1000
    assert all(len(chunk) <= 200 for chunk in chunks)
