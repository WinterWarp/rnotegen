"""The math command: TeX through matplotlib mathtext into a normalized vector image."""

import sys
import xml.etree.ElementTree as ET

import pytest

from rnotegen.api import build
from rnotegen.builder import Builder, BuildError
from rnotegen.dsl import parse_script
from rnotegen.model import VectorImageEl

SVG_NS = "{http://www.w3.org/2000/svg}"


def run(src):
    b = Builder()
    doc = b.run_script(src)
    return b, doc


def math_images(doc):
    return [e for e in doc.elements if isinstance(e, VectorImageEl)]


# ---------------------------------------------------------------- no matplotlib needed


def test_math_strings_keep_tex_backslashes():
    (st,) = parse_script(r'math "\nabla\theta + \\ \frac{a}{b} \"q\""')
    assert st.pos[0].value == r'\nabla\theta + \\ \frac{a}{b} "q"'
    (st,) = parse_script('math size=20\n\\nabla \\frac{1}{2}\nend\n')
    assert st.body == [r"\nabla \frac{1}{2}"]


@pytest.mark.parametrize("tex,fixed", [
    # \, after a bare \int is swallowed by the sign's overhang
    (r"\int\,f(x)\,dx", r"\int\!\!\!\quad f(x)\,dx"),
    (r"\int f(x)\,dx", r"\int\!\!\!\quad f(x)\,dx"),
    (r"\oint\,f", r"\oint\!\!\!\quad f"),
    # after limits a plain \  works
    (r"\int_0^1\,x\,dx", r"\int_0^1\ x\,dx"),
    (r"\int_0^1 x\,dx", r"\int_0^1\ x\,dx"),
    (r"\int_{0}^{\pi} \sin x\,dx", r"\int_{0}^{\pi}\ \sin x\,dx"),
    (r"\iint_D f\,dA", r"\iint_D\ f\,dA"),
    # explicit spacing and lone integrals stay as written
    (r"\int\;f", r"\int\;f"), (r"\int\ f", r"\int\ f"), (r"\int\quad f", r"\int\quad f"), (r"\int\!f", r"\int\!f"),
    (r"\int", r"\int"), (r"{\int}", r"{\int}"),
    # function names after a letter, digit, } or ) get a thin space
    (r"x\sin x", r"x\,\sin x"), (r"2\cos x", r"2\,\cos x"), (r"e^{x}\ln x", r"e^{x}\,\ln x"),
    (r"(x+1)\sin x", r"(x+1)\,\sin x"), (r"\pi\sin x", r"\pi\,\sin x"), (r"x\sinh x", r"x\,\sinh x"),
    (r"y = x\log_2 x", r"y = x\,\log_2 x"),
    (r"\sin x", r"\sin x"), (r"a\cdot\sin x", r"a\cdot\sin x"), (r"(\sin x)", r"(\sin x)"),
    (r"\arcsin x", r"\arcsin x"), (r"x\,\sin x", r"x\,\sin x"), (r"\sin^2 x", r"\sin^2 x"),
    # \left[ ... \right]_a^b around flat content: plain brackets
    (r"\left[x^2-2x\right]_0^3", r"[\,x^2-2x\,]_0^3"),
    (r"\left[ x^2 \right]^{b}_{a}", r"[\, x^2 \,]^{b}_{a}"),
    (r"\left[\frac{x^3}{3}\right]_0^1", r"\left[\frac{x^3}{3}\right]_0^1"),  # tall content keeps sized brackets
    (r"\left[x\right]", r"\left[x\right]"),
    # \overline after a decimal point
    (r"0.\overline{3}", r"0.\hspace{-0.2}\overline{3}"), (r"3.\dot{3}", r"3.\hspace{-0.2}\dot{3}"),
    (r"0. \overline{142857}", r"0.\hspace{-0.2} \overline{142857}"),
    (r"1.2\overline{34}", r"1.2\overline{34}"), (r"x.\overline{3}", r"x.\overline{3}"),
])
def test_mathtext_quirks_are_rewritten(tex, fixed):
    from rnotegen.mathtex import fix_mathtext

    assert fix_mathtext(tex) == fixed


def test_quirk_rewrites_only_touch_math_segments():
    from rnotegen.mathtex import fix_mathtext

    assert fix_mathtext(r"cost 2.\$ and $x\sin x$") == r"cost 2.\$ and $x\,\sin x$"


def test_math_without_matplotlib_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    with pytest.raises(BuildError) as e:
        run('h1 "x"\nmath "x^2"\n')
    (err,) = e.value.errors
    assert err.line == 2 and "matplotlib" in str(err)


# ---------------------------------------------------------------- rendering (needs matplotlib)


@pytest.fixture
def mpl():
    return pytest.importorskip("matplotlib")


def test_math_is_a_normalized_vector_image_without_background(mpl):
    _, doc = run(r'math "\frac{a}{b} + \sqrt{x^2}"' + "\n")
    (img,) = math_images(doc)
    svg = img.svg
    assert "#ffffff" not in svg.lower() and "white" not in svg
    assert "<use" not in svg and "<style" not in svg and "style=" not in svg and "<?xml" not in svg
    root = ET.fromstring(svg)
    fills = {p.get("fill") for p in root.iter(SVG_NS + "path")}
    assert fills == {"#1e1e1e"}  # the default text color
    assert not list(root.iter(SVG_NS + "rect"))


def test_math_size_follows_defaults_and_size_option(mpl):
    _, doc = run('math "x+1"\ndefaults size=32\nmath "x+1"\nmath "x+1" size=8\n')
    a, b, c = math_images(doc)
    assert b.h == pytest.approx(2 * a.h, rel=0.02) and b.w == pytest.approx(2 * a.w, rel=0.02)
    assert c.h == pytest.approx(a.h / 2, rel=0.02)
    assert 14 < a.h < 26  # about one line of 16px text


def test_math_color_option(mpl):
    _, doc = run('math "x" color=red\n')
    (img,) = math_images(doc)
    assert {p.get("fill") for p in ET.fromstring(img.svg).iter(SVG_NS + "path")} == {"#ff0000"}


def test_math_block_and_mixed_text(mpl):
    b, doc = run('math id=a\n\\frac{1}{2}\nend\nmath "Area $A = \\pi r^2$" id=m\nmath "A = \\pi r^2" id=n\n')
    assert len(math_images(doc)) == 3
    assert b.ids["m"].bbox().w > b.ids["n"].bbox().w + 20  # "Area " is rendered too


def test_math_in_flow_and_in_groups(mpl):
    b, doc = run('para "Solve:" id=p\nmath "x^2 = 4" id=m\n'
                 'group at=100,400 id=g\n  text "a)" at=0,0 id=lbl\n  math "x = 2" at=30,0 id=gm\nend\n')
    p, m = b.ids["p"].bbox(), b.ids["m"].bbox()
    assert m.y0 >= p.y1 and m.x0 == pytest.approx(56)
    gm = b.ids["gm"].bbox()
    assert (gm.x0, gm.y0) == pytest.approx((130, 400))
    _, doc = run('group\n  text "b)" at=0,0\n  math "y = 3" at=30,0\nend\n')
    (img,) = math_images(doc)
    assert img.x == pytest.approx(56 + 30)


def test_math_baseline_lines_up_with_text(mpl):
    b, _ = run('text "Area:" at=60,100 id=t\n'
               r'math "\frac{1}{2}bh" right_of=t align=baseline gap=8 id=m' + "\n"
               r'math "\sum_i x_i" at=t.baseline+200,0 anchor=baseline id=s' + "\n"
               'text "where" left_of=s align=baseline id=w\n')
    base = b.point("t.baseline", 1)[1]
    assert b.point("m.baseline", 1)[1] == pytest.approx(base, abs=0.01)
    assert b.point("s.baseline", 1)[1] == pytest.approx(base, abs=0.01)
    assert b.point("w.baseline", 1)[1] == pytest.approx(base, abs=0.01)
    t, m = b.ids["t"].bbox(), b.ids["m"].bbox()
    assert m.x0 == pytest.approx(t.x1 + 8)
    assert m.y0 <= t.y0 + 0.01 and m.y1 >= t.y1 - 0.01  # a math box is at least one text line tall
    assert 100 < base < 100 + 16  # the first line's baseline sits inside the text line


def test_math_and_text_at_the_same_y_share_a_baseline(mpl):
    b, _ = run('text "Label" at=60,100 id=t\nmath "x^2 + y^2" at=140,100 id=m\n')
    assert b.point("m.baseline", 1)[1] == pytest.approx(b.point("t.baseline", 1)[1], abs=0.01)


def test_baseline_of_element_without_text_is_an_error():
    with pytest.raises(BuildError) as e:
        run("rect at=10,10 size=20x20 id=r\ntext \"x\" at=r.baseline\n")
    assert "baseline" in str(e.value)


def test_math_errors_carry_line_numbers(mpl):
    with pytest.raises(BuildError) as e:
        run('h1 "x"\nmath "\\frac{a"\n')
    (err,) = e.value.errors
    assert err.line == 2 and "TeX" in err.msg


def test_quirk_fixes_change_the_rendering_and_raw_turns_them_off(mpl):
    b, _ = run(r'math "0.\overline{3}" id=a' + "\n" + r'math "0.\overline{3}" raw id=b' + "\n" + 'math "0.3" id=c\n')
    fixed, raw, plain = (b.ids[k].bbox().w for k in "abc")
    assert raw > fixed and fixed == pytest.approx(plain, abs=1.0)


def test_math_renders_in_rnote(tmp_path, render_with_rnote, mpl):
    res = build('document size=a5 layout=pages\nmath "\\int_0^1 x^2\\,dx = \\frac{1}{3}" at=100,100 size=40 id=m\n',
                "script", str(tmp_path / "m.rnote"))
    assert res.ok, res.report()
    pages = render_with_rnote(tmp_path / "m.rnote")
    if pages is None:
        pytest.skip("rnote-cli or Pillow not available")
    im = pages[0]
    dark = sum(1 for y in range(90, 200) for x in range(90, 400) if sum(im.getpixel((x, y))) < 200)
    assert dark > 300
