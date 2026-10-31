import io

from PIL import Image

from pageindex_rag.indexing.summarize import MAX_VISION_SIDE, prepare_for_vision, should_describe
from pageindex_rag.models import ImageRecord

from .conftest import make_noisy_png, make_png


def record(mime: str = "image/png") -> ImageRecord:
    return ImageRecord(id="img-1", file="images/img-1.png", mime=mime)


def test_a_flat_colour_chart_is_described_even_though_its_file_is_tiny():
    chart = make_png(480, 240)  # a few hundred bytes: it compresses to almost nothing
    assert len(chart) < 4096
    assert should_describe(record(), chart)


def test_icons_and_rules_are_skipped_by_pixel_size():
    assert not should_describe(record(), make_png(16, 16))  # an icon
    assert not should_describe(record(), make_png(600, 8))  # a divider line
    assert not should_describe(record(), make_noisy_png(20, 400))  # large file, but a thin strip
    assert should_describe(record(), make_noisy_png(32, 32))


def test_unreadable_or_unsupported_images_are_skipped():
    assert not should_describe(record(), b"not an image at all")
    assert not should_describe(record("image/emf"), make_png(200, 200))


def test_large_images_are_shrunk_before_they_are_sent():
    big = make_png(3200, 2000)

    prepared = prepare_for_vision(big)

    assert prepared is not None
    data, mime = prepared
    with Image.open(io.BytesIO(data)) as shrunk:
        assert max(shrunk.size) == MAX_VISION_SIDE
        assert abs(shrunk.size[0] / shrunk.size[1] - 1.6) < 0.01  # the shape is kept
    assert mime == "image/jpeg"  # opaque images are re-encoded as JPEG


def test_small_images_keep_their_size_and_transparency_keeps_png():
    with Image.open(io.BytesIO(make_png(200, 100))) as image:
        buffer = io.BytesIO()
        image.convert("RGBA").save(buffer, "PNG")

    data, mime = prepare_for_vision(buffer.getvalue())

    assert mime == "image/png"
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == (200, 100)
