from toc_rag.outline import node_markdown, outline_rows
from toc_rag.sessions import describe_duration

from .test_agent import make_ships


def test_outline_rows_are_depth_first_with_citation_refs():
    rows = outline_rows(make_ships())
    assert [(r.ref, r.title, r.depth) for r in rows] == [
        ("ships:1", "Ship", 0),
        ("ships:1.1", "Crew", 1),
        ("ships:1.2", "Engines", 1),
    ]


def test_node_markdown_inlines_images_and_lists_subsections():
    index = make_ships()
    nodes = index.node_map()
    index.images[0].description = "The crew [on] deck\nat dawn"

    crew = node_markdown(index, nodes["1.1"], lambda record: f"/files/{record.file}")
    assert crew == "The captain is Ada.\n\n![The crew on deck at dawn](/files/images/img-1.png)"

    nodes["1.2"].text = ""  # a text-less leaf stays empty
    assert node_markdown(index, nodes["1.2"], lambda record: "") == ""

    ship = node_markdown(index, nodes["1"], lambda record: "")
    assert ship.startswith("_This section is divided into subsections:_")
    assert "- **Crew**: no summary" in ship and "- **Engines**: no summary" in ship


def test_unknown_image_placeholders_disappear():
    index = make_ships()
    index.node_map()["1.2"].text = "Before [image:img-9] after"
    assert node_markdown(index, index.node_map()["1.2"], lambda record: "") == "Before  after"


def test_describe_duration():
    assert describe_duration(7200) == "2 hours"
    assert describe_duration(3900) == "1 hour 5 minutes"
    assert describe_duration(720) == "12 minutes"
    assert describe_duration(59) == "under a minute"
