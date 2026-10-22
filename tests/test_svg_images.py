"""Embedded SVGs must render in Rnote, which pastes svg_data inside its own <svg> before librsvg parses it."""

import os
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

from rnotegen.api import build
from rnotegen.builder import Builder, BuildError, svg_intrinsic_size
from rnotegen.model import Document, VectorImageEl
from rnotegen.preview import document_svg, write_png
from rnotegen.rnoteformat import write_document

DATA = os.path.join(os.path.dirname(__file__), "data")
MPL_SVG = os.path.join(DATA, "matplotlib_plot.svg")  # real matplotlib output: XML declaration, DOCTYPE, defs + use, CSS
SVG_NS = "{http://www.w3.org/2000/svg}"


def vector_images(doc):
    return [e for e in doc.elements if isinstance(e, VectorImageEl)]


def build_images(src):
    b = Builder(base_dir=DATA)
    doc = b.run_script(src)
    return b, doc, vector_images(doc)


def ids_and_refs(svg):
    ids = re.findall(r'\bid="([^"]+)"', svg)
    refs = re.findall(r"url\(#([^)]+)\)", svg) + re.findall(r'href="#([^"]+)"', svg)
    return ids, refs


def elements(svg, tag):
    return list(ET.fromstring(svg).iter(SVG_NS + tag))


def blue_pixels(im, box):
    """Pixels close to matplotlib's default line color #1f77b4 inside box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = im.getpixel((x, y))
            if abs(r - 0x1F) < 40 and abs(g - 0x77) < 40 and abs(b - 0xB4) < 40:
                n += 1
    return n


def test_matplotlib_svg_is_normalized_on_import():
    _, _, (img,) = build_images('image "matplotlib_plot.svg" width=300\n')
    svg = img.svg
    for bad in ("<?xml", "<!DOCTYPE", "<!--", "<metadata", "<use", "<style", "style=", "class=", "rdf:"):
        assert bad not in svg, f"{bad} left in normalized svg"
    root = ET.fromstring(svg)
    assert root.tag == SVG_NS + "svg"
    assert 'fill="#ffffff"' in svg  # style="fill: #ffffff" became a presentation attribute
    assert 'stroke-linejoin="round"' in svg  # from the <style> sheet: *{stroke-linejoin: round}
    assert img.intrinsic == pytest.approx((192.0, 144.0))  # 144pt x 108pt
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("192", "144", "0 0 144 108")
    ids, refs = ids_and_refs(svg)
    assert len(ids) == len(set(ids)) and set(refs) <= set(ids)
    # the expanded <use> glyphs and markers are real paths now
    assert len(elements(svg, "path")) > 13


def test_svg_block_from_inline_text_is_normalized():
    src = ('svg at=10,10 width=40\n<?xml version="1.0"?>\n<!DOCTYPE svg [ <!ENTITY red "#ff0000"> ]>\n<!-- made by hand -->\n'
           '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           'width="10" height="10"><metadata>x</metadata><rect width="5" height="5" fill="&red;" inkscape:label="L"/>'
           '</svg>\nend\n')
    _, _, (img,) = build_images(src)
    assert "<?xml" not in img.svg and "<!" not in img.svg and "inkscape" not in img.svg and "metadata" not in img.svg
    (rect,) = elements(img.svg, "rect")
    assert rect.get("fill") == "#ff0000"


def test_css_becomes_presentation_attributes_with_cascade():
    src = ('svg at=10,10\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
           '<style>/* sheet */ rect { fill: blue } .a { fill: red; } #b { fill: green } * { stroke: black }'
           ' g > rect.c { opacity: 0.5 } .imp { fill: gray !important }</style>'
           '<rect width="1" height="1"/><rect class="a" width="1" height="1"/>'
           '<rect class="a" id="b" width="1" height="1"/>'
           '<rect class="a" style="fill: yellow; stroke-width: 3" fill="pink" width="1" height="1"/>'
           '<rect class="imp" style="fill: yellow" width="1" height="1"/>'
           '<g><rect class="c" width="1" height="1"/></g></svg>\nend\n')
    _, _, (img,) = build_images(src)
    rects = elements(img.svg, "rect")
    assert [r.get("fill") for r in rects[:5]] == ["blue", "red", "green", "yellow", "gray"]
    assert all(r.get("stroke") == "black" for r in rects)
    assert rects[3].get("stroke-width") == "3"
    assert rects[5].get("opacity") == "0.5" and rects[0].get("opacity") is None
    assert "<style" not in img.svg and "class=" not in img.svg and "style=" not in img.svg


def test_use_references_are_expanded():
    src = ('svg at=10,10\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           'width="20" height="20"><defs><path id="p" d="M0 0 L5 0" style="stroke: blue"/>'
           '<g id="two"><use xlink:href="#p"/><use href="#p" y="2"/></g></defs>'
           '<use xlink:href="#two" x="3" y="4" style="stroke-width: 2"/>'
           '<symbol id="s" viewBox="0 0 2 2"><circle cx="1" cy="1" r="1"/></symbol>'
           '<use href="#s" x="10" width="4" height="4"/></svg>\nend\n')
    _, _, (img,) = build_images(src)
    assert "<use" not in img.svg and "<symbol" not in img.svg
    paths = elements(img.svg, "path")
    assert len(paths) == 2 and all(p.get("d") == "M0 0 L5 0" and p.get("stroke") == "blue" for p in paths)
    root = ET.fromstring(img.svg)
    outer = [g for g in root.iter(SVG_NS + "g") if g.get("stroke-width") == "2"]
    assert len(outer) == 1 and outer[0].get("transform") == "translate(3 4)"
    (sym,) = [e for e in root.iter(SVG_NS + "svg") if e is not root]
    assert (sym.get("viewBox"), sym.get("width"), sym.get("height")) == ("0 0 2 2", "4", "4")
    assert len(list(sym.iter(SVG_NS + "circle"))) == 1


def test_ids_are_prefixed_per_image():
    one = ('svg at={x},10\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><defs>'
           '<linearGradient id="g"><stop offset="0" stop-color="{c}"/></linearGradient></defs>'
           '<rect width="10" height="10" fill="url(#g)"/></svg>\nend\n')
    _, _, (a, b) = build_images(one.replace("{x}", "10").replace("{c}", "red") + one.replace("{x}", "40").replace("{c}", "blue"))
    ids_a, refs_a = ids_and_refs(a.svg)
    ids_b, refs_b = ids_and_refs(b.svg)
    assert len(ids_a) == len(ids_b) == 1 and ids_a != ids_b
    assert refs_a == ids_a and refs_b == ids_b


def test_svg_intrinsic_size_units():
    assert svg_intrinsic_size('<svg width="72pt" height="1in"/>') == pytest.approx((96, 96))
    assert svg_intrinsic_size('<svg width="50mm" viewBox="0 0 10 5"/>') == pytest.approx((188.976, 94.488), abs=1e-3)
    assert svg_intrinsic_size('<svg width="100%" height="100%" viewBox="0 0 30 20"/>') == (30, 20)
    assert svg_intrinsic_size('<svg width=" 12.5px " height="1e1"/>') == (12.5, 10)
    _, _, (img,) = build_images('svg at=0,0\n<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="25mm" '
                                'viewBox="0 0 10 5"><rect width="10" height="5"/></svg>\nend\n')
    root = ET.fromstring(img.svg)
    assert float(root.get("width")) == pytest.approx(188.976, abs=1e-3)
    assert img.intrinsic == pytest.approx((188.976, 94.488), abs=1e-3)


def test_invalid_svg_is_an_error_with_line_number():
    res = build('h1 "x"\nsvg at=10,10\n<svg xmlns="http://www.w3.org/2000/svg" width="5" height="5"><rect></svg>\nend\n',
                "script", None)
    assert not res.ok
    assert res.errors[0].startswith("line 2:") and "svg" in res.errors[0]


# ---------------------------------------------------------------- preview and the Rnote wrapper (bug 2)


def test_preview_renders_images_through_rnotes_wrapper():
    """The preview must fail the way Rnote does: svg_data pasted inside another <svg>."""
    with open(MPL_SVG, encoding="utf-8") as f:
        raw = f.read()
    doc = Document()
    doc.elements.append(VectorImageEl(raw, 10, 10, 192, 144, (192, 144)))
    preview = document_svg(doc)
    assert "data:image/svg+xml" not in preview
    with pytest.raises(ET.ParseError):
        ET.fromstring(preview)  # an XML declaration inside the document is not well-formed, as in Rnote


def test_build_reports_svg_data_that_does_not_parse_once_wrapped():
    with open(MPL_SVG, encoding="utf-8") as f:
        raw = f.read()
    b = Builder()
    el = VectorImageEl(raw, 10, 10, 192, 144, (192, 144))
    b.doc.elements.append(el)
    b.src_line[id(el)] = 7
    with pytest.raises(BuildError) as e:
        b.finish()
    (err,) = e.value.errors
    assert err.line == 7 and "Rnote" in err.msg


def test_matplotlib_svg_preview_regression(tmp_path):
    b, doc, (img,) = build_images('document size=a5 layout=pages\nimage "matplotlib_plot.svg" at=100,100 width=300\n')
    preview = document_svg(doc)
    root = ET.fromstring(preview)  # the wrapped image is well-formed
    assert "data:image/svg+xml" not in preview
    nested = [e for e in root.iter(SVG_NS + "svg") if e is not root]
    assert nested, "vector images are drawn inline, nested the way Rnote nests them"
    if not (shutil.which("rsvg-convert") or _has_cairosvg()):
        pytest.skip("no SVG rasterizer")
    pytest.importorskip("PIL")
    from PIL import Image

    out = tmp_path / "p.png"
    write_png(doc, str(out))
    im = Image.open(out).convert("RGB")
    assert blue_pixels(im, (100, 100, 400, 325)) > 100  # the plotted line and its markers


def test_matplotlib_svg_renders_in_rnote(tmp_path, render_with_rnote):
    res = build('document size=a5 layout=pages\nimage "matplotlib_plot.svg" at=100,100 width=300\n', "script",
                str(tmp_path / "plot.rnote"), base_dir=DATA)
    assert res.ok, res.report()
    pages = render_with_rnote(tmp_path / "plot.rnote")
    if pages is None:
        pytest.skip("rnote-cli or Pillow not available")
    assert blue_pixels(pages[0], (100, 100, 400, 325)) > 100


def test_two_images_in_one_file_render_in_rnote(tmp_path, render_with_rnote):
    doc = Builder(base_dir=DATA).run_script('document size=a5 layout=pages\nimage "matplotlib_plot.svg" at=40,40 width=200\n'
                                            'image "matplotlib_plot.svg" at=40,300 width=200\n')
    p = tmp_path / "two.rnote"
    write_document(doc, str(p))
    pages = render_with_rnote(p)
    if pages is None:
        pytest.skip("rnote-cli or Pillow not available")
    assert blue_pixels(pages[0], (40, 40, 240, 190)) > 50 and blue_pixels(pages[0], (40, 300, 240, 450)) > 50


def _has_cairosvg():
    try:
        import cairosvg  # noqa: F401
        return True
    except ImportError:
        return False
