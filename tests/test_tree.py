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
