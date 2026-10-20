"""SVG pieces for the SGI themes, generated from a theme's colours.

Stylesheets can only draw bevels, check marks and the like from images, so each
SGI theme writes its pieces as small SVGs into a cache folder named after a hash
of its colours (written once, then reused). Most are used as 9-slice
``border-image``s, so the bevels stay pixel-exact at any widget size, and SVG keeps
them sharp at any display scale. The shapes were traced from IRIX screenshots.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

# Bump when a piece's drawing changes, so caches from older versions aren't reused.
ASSET_VERSION = 1


# -- primitives -----------------------------------------------------------------

def _rect(x, y, w, h, c) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{c}" shape-rendering="crispEdges"/>'


def svg(w: int, h: int, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">{"".join(body)}</svg>')


def bevel(w, h, face, tl_out, tl_in, br_in, br_out, ring=None) -> list[str]:
    """Motif two-step bevel: outer + inner edge top-left, inner + outer bottom-right.
    ``ring`` adds a 1px outline around it (Motif buttons)."""
    o = 1 if ring else 0
    s = [_rect(0, 0, w, h, ring)] if ring else []
    x0, y0, x1, y1 = o, o, w - o, h - o
    iw, ih = x1 - x0, y1 - y0
    s += [_rect(x0, y0, iw, ih, face),
          _rect(x0, y0, iw, 1, tl_out), _rect(x0, y0, 1, ih, tl_out),
          _rect(x0 + 1, y0 + 1, iw - 2, 1, tl_in), _rect(x0 + 1, y0 + 1, 1, ih - 2, tl_in),
          _rect(x0, y1 - 1, iw, 1, br_out), _rect(x1 - 1, y0, 1, ih, br_out),
          _rect(x0 + 1, y1 - 2, iw - 2, 1, br_in), _rect(x1 - 2, y0 + 1, 1, ih - 2, br_in)]
    return s


# -- check mark -------------------------------------------------------------------
# A brush-like mark (box coordinates, 16x16 box at 1,1): a rounded lobe on the left
# with a notch where it meets the long arm, a point below, and a long arm running
# over the box's top-right border. Bright red facing the light, deepening to maroon
# toward the tip; pink highlight on the lit edges, hard and soft drop shadows.
_TICK_SHAPE = ("M6.4 11.0 C5.4 9.8 2.2 8.4 2.1 6.8 C2.0 5.2 3.0 4.2 4.0 4.2 "
               "C4.9 4.2 5.3 5.0 5.4 5.8 L5.9 6.6 C6.6 5.4 7.4 4.6 8.4 3.9 "
               "C10.6 2.6 13.0 1.2 15.4 0.4 L16.0 1.8 C13.2 3.2 11.4 4.6 9.9 6.0 "
               "C8.9 7.0 8.0 8.2 7.6 9.1 C7.3 10.0 6.9 10.8 6.4 11.0 Z")
_TICK_HL = ("M2.5 5.9 C2.7 5.0 3.3 4.5 4.1 4.5 "           # top of the lobe
            "M6.9 5.2 C7.6 4.5 8.4 3.9 9.4 3.3 "           # start of the long arm
            "M11.4 2.0 C12.8 1.3 14.0 0.8 15.3 0.4")       # tip


def _tick(colour, highlight, shadow, deep=None, tip=None) -> list[str]:
    deep, tip = deep or shadow, tip or shadow
    gid = "tick" + colour.lstrip("#")
    g = "<g transform='translate(1,1)'>"
    return [
        f'<defs><linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="6" y1="0" x2="16" y2="0">'
        f'<stop offset="0" stop-color="{colour}"/><stop offset="0.45" stop-color="{colour}"/>'
        f'<stop offset="0.75" stop-color="{deep}"/><stop offset="1" stop-color="{tip}"/></linearGradient></defs>',
        g + f'<path d="{_TICK_SHAPE}" fill="#000000" opacity="0.22" transform="translate(1.7,1.8)"/></g>',
        g + f'<path d="{_TICK_SHAPE}" fill="{shadow}" opacity="0.95" transform="translate(0.9,1.0)"/></g>',
        g + f'<path d="{_TICK_SHAPE}" fill="url(#{gid})"/></g>',
        g + f'<path d="{_TICK_HL}" fill="none" stroke="{highlight}" stroke-width="0.6" '
            'stroke-linecap="round" opacity="0.85"/></g>',
    ]


# -- radio buttons ---------------------------------------------------------------

def _diamond(face, light, dark) -> list[str]:
    """Raised diamond in an 18x18 box: outline, highlight inside the upper edges,
    a heavier dark line inside the lower edges (shadow)."""
    return [
        f'<path d="M9 1 L17 9 L9 17 L1 9 Z" fill="{face}"/>',
        f'<path d="M2.4 9 L9 2.4 L15.6 9" fill="none" stroke="{light}" stroke-width="1"/>',
        f'<path d="M2.7 9.7 L9 16 L15.3 9.7" fill="none" stroke="{dark}" stroke-width="1.3"/>',
        f'<path d="M9 1 L17 9 L9 17 L1 9 Z" fill="none" stroke="{dark}" stroke-width="1.4" stroke-linejoin="miter"/>',
    ]


def _radio_arrow(t, disabled=False) -> list[str]:
    """The blue 'play' arrow inside a selected radio diamond: a light rim all round,
    navy inner edges on the left and top, blue fill, black shadow outside the rim."""
    tri = "M9.4 4.6 L13.8 7.9 L9.4 11.2 Z"
    if disabled:
        return [f'<path d="{tri}" fill="{t.shadow2}"/>']
    return [
        f'<path d="{tri}" fill="#000000" transform="translate(1.6,1.7)"/>',
        f'<path d="{tri}" fill="{t.radio_glow}" stroke="{t.radio_glow}" stroke-width="1.7" stroke-linejoin="round"/>',
        f'<path d="{tri}" fill="{t.radio}"/>',
        f'<path d="M9.95 10.7 L9.95 5.4 L13.0 7.7" fill="none" stroke="{t.radio_edge}" stroke-width="1.0"/>',
    ]


# -- menus -------------------------------------------------------------------------

def _cabinet(ink) -> list[str]:
    """The Toolchest cabinet that marks a submenu: an outlined lid and body, each
    with a hard shadow to the right and below."""
    return [
        _rect(0, 0, 11, 1, ink), _rect(0, 3, 11, 1, ink), _rect(0, 0, 1, 4, ink), _rect(10, 0, 1, 4, ink),
        _rect(11, 2, 1, 2, ink), _rect(2, 4, 10, 1, ink),
        _rect(0, 5, 11, 1, ink), _rect(0, 14, 11, 1, ink), _rect(0, 5, 1, 10, ink), _rect(10, 5, 1, 10, ink),
        _rect(11, 6, 1, 9, ink), _rect(2, 15, 10, 1, ink),
    ]


# -- scrollbars --------------------------------------------------------------------

def _sb_bevel(t, pressed=False) -> list[str]:
    """12x12 scrollbar button / thumb, sliced 3px: 1px dark and two light steps on the
    top/left, three darkening steps (mid, dark, near-black) on the bottom/right, mitered."""
    tl = (t.shadow, t.hilite, t.hilite2)
    br = (t.shadow2, t.shadow, t.wheel_lo)
    if pressed:
        tl, br = (t.shadow, t.wheel_lo, t.shadow), (t.hilite2, t.hilite2, t.hilite)
    out = [_rect(0, 0, 12, 12, t.wheel_face)]
    for i, c in enumerate(br):
        j = 2 - i
        out += [_rect(0, 11 - j, 12, 1, c), _rect(11 - j, 0, 1, 12, c)]
    for i, c in enumerate(tl):
        out += [_rect(i, i, 12 - 2 * i - (3 - i), 1, c), _rect(i, i, 1, 12 - 2 * i - (3 - i), c)]
    return out


def _flat_arrow(direction, colour) -> str:
    pts = {"up": "5.5 1 10.5 9 0.5 9", "down": "0.5 2 10.5 2 5.5 10",
           "left": "1 5.5 9 0.5 9 10.5", "right": "2 0.5 10 5.5 2 10.5"}[direction]
    return svg(11, 11, [f'<polygon points="{pts}" fill="{colour}"/>'])


# -- all pieces --------------------------------------------------------------------

DEFAULT_RING = 5          # px from the default frame's edge to the button (frame 2 + gap 3)
DEFAULT_SLICE = DEFAULT_RING + 3                 # + the button's own ring and bevel


def _default(t, face, tl_out, tl_in, br_in, br_out) -> str:
    n, o = 24, DEFAULT_RING
    frame = bevel(n, n, t.bg, t.shadow, t.shadow2, t.hilite2, t.hilite)       # sunken
    return svg(n, n, frame + [f"<g transform='translate({o},{o})'>"]
               + bevel(n - 2 * o, n - 2 * o, face, tl_out, tl_in, br_in, br_out, ring=t.ring) + ["</g>"])


def pieces(t) -> dict[str, str]:
    """name -> SVG text for every piece the SGI stylesheet uses."""
    hl, hl2, sh2, sh = t.hilite, t.hilite2, t.shadow2, t.shadow
    face, well, ring, lo = t.tile, t.well, t.ring, t.wheel_lo
    box = "<g transform='translate(1,1)'>"
    return {
        # buttons, wells and panels: 12x12, sliced 2-3px
        "raised": svg(12, 12, bevel(12, 12, face, hl, hl2, sh2, sh, ring=ring)),
        "raised_hover": svg(12, 12, bevel(12, 12, t.tile_hover, hl, hl2, sh2, sh, ring=ring)),
        "pressed": svg(12, 12, bevel(12, 12, t.pressed, sh, sh2, hl2, hl, ring=ring)),
        # the default button, as Indigo Magic / Motif draw it: the usual button inside
        # a sunken frame, with a gap of background between them (24x24, sliced 8px)
        "default": _default(t, face, hl, hl2, sh2, sh),
        "default_hover": _default(t, t.tile_hover, hl, hl2, sh2, sh),
        "default_pressed": _default(t, t.pressed, sh, sh2, hl2, hl),
        "sunken": svg(12, 12, bevel(12, 12, well, sh, sh2, hl2, hl)),
        "sunken_desktop": svg(12, 12, bevel(12, 12, getattr(t, "grid_bg", "") or t.desktop, sh, sh2, hl2, hl)),
        "sunken_list": svg(12, 12, bevel(12, 12, getattr(t, "list_bg", "") or well, sh, sh2, hl2, hl)),
        "entry": svg(12, 12, bevel(12, 12, t.entry, sh, sh2, hl2, hl)),
        "panel": svg(12, 12, bevel(12, 12, t.bg, hl, hl2, sh2, sh)),
        "option": svg(14, 8, bevel(14, 8, face, hl, hl, sh, sh)),
        # slider: a deep channel with a glint along its top edge; the thumb sits flush
        # inside it, black ends and a centre groove over the channel's grey, mitered bevels
        "trough": svg(12, 15, [
            _rect(0, 0, 12, 15, t.wheel_face),
            _rect(0, 0, 12, 1, sh2), _rect(0, 1, 12, 1, sh), _rect(0, 2, 12, 1, lo),
            _rect(0, 3, 12, 1, hl),
            _rect(0, 11, 12, 1, sh), _rect(0, 12, 12, 1, lo),
            _rect(0, 13, 12, 1, t.bg), _rect(0, 14, 12, 1, hl),
            _rect(0, 0, 1, 13, sh2), _rect(1, 1, 1, 12, sh), _rect(2, 2, 1, 10, lo),
            _rect(9, 2, 1, 11, lo), _rect(10, 1, 1, 13, hl2), _rect(11, 0, 1, 15, hl)]),
        "slider_thumb": svg(30, 9, [
            _rect(0, 0, 30, 9, t.wheel_face),
            _rect(28, 1, 1, 8, sh), _rect(27, 2, 1, 7, sh),
            _rect(3, 7, 26, 1, sh), _rect(2, 8, 27, 1, sh),
            _rect(1, 0, 28, 1, hl2), _rect(1, 1, 27, 1, hl2),
            _rect(1, 0, 1, 9, hl2), _rect(2, 0, 1, 8, hl2),
            _rect(0, 0, 1, 9, lo), _rect(29, 0, 1, 9, lo),
            _rect(14, 1, 1, 8, hl), _rect(15, 1, 1, 8, lo)]),
        "slider_thumb_dis": svg(30, 9, [
            _rect(0, 0, 30, 9, t.bg), _rect(0, 0, 1, 9, sh2), _rect(29, 0, 1, 9, sh2),
            _rect(14, 1, 1, 8, hl2), _rect(15, 1, 1, 8, sh2)]),
        # check boxes: outlined raised box; the face lights up when checked
        "check_off": svg(18, 18, [box] + bevel(16, 16, t.check_off, hl, hl2, sh2, sh, ring=ring) + ["</g>"]),
        "check_on": svg(18, 18, [box] + bevel(16, 16, t.check_face, hl, hl2, sh2, sh, ring=ring) + ["</g>"]
                        + _tick(t.tick, t.tick_hl, t.tick_sh, t.tick_deep, t.tick_tip)),
        "check_dis": svg(18, 18, [box] + bevel(16, 16, t.bg, hl2, hl2, sh2, sh2, ring=sh2) + ["</g>"]),
        "check_dis_on": svg(18, 18, [box] + bevel(16, 16, t.bg, hl2, hl2, sh2, sh2, ring=sh2) + ["</g>"]
                            + _tick(t.accent_dis, t.accent_dis, t.accent_dis)),
        # radio buttons
        "radio_off": svg(18, 18, _diamond(t.radio_face, hl, ring)),
        "radio_on": svg(18, 18, _diamond(t.radio_face, hl, ring) + _radio_arrow(t)),
        "radio_dis": svg(18, 18, _diamond(t.bg, hl2, sh2)),
        "radio_dis_on": svg(18, 18, _diamond(t.bg, hl2, sh2) + _radio_arrow(t, disabled=True)),
        # menus
        "submenu": svg(12, 16, _cabinet(t.text)),
        "submenu_sel": svg(12, 16, _cabinet(t.select_text)),
        # scrollbars: troughs carry a 3px transparent gap on the side facing the view
        "sb_raised": svg(12, 12, _sb_bevel(t)),
        "sb_pressed": svg(12, 12, _sb_bevel(t, pressed=True)),
        "sb_trough_v": svg(12, 12, [_rect(3, 0, 9, 12, t.wheel_face), _rect(3, 0, 9, 1, sh), _rect(3, 0, 1, 12, sh),
                                    _rect(11, 0, 1, 12, lo)]),
        "sb_trough_h": svg(12, 12, [_rect(0, 3, 12, 9, t.wheel_face), _rect(0, 3, 12, 1, sh), _rect(0, 3, 1, 9, sh),
                                    _rect(0, 11, 12, 1, lo)]),
        "sb_grip_v": svg(11, 10, sum(([_rect(0, y, 11, 1, hl), _rect(0, y + 1, 11, 1, sh)] for y in (0, 4, 8)), [])),
        "sb_grip_h": svg(10, 11, sum(([_rect(x, 0, 1, 11, hl), _rect(x + 1, 0, 1, 11, sh)] for x in (0, 4, 8)), [])),
        **{f"sb_arrow_{d}": _flat_arrow(d, t.sb_arrow or sh) for d in ("up", "down", "left", "right")},
        # notebook tabs: slanted sides, sliced 8px left/right
        "tab": svg(40, 22, [
            f'<path d="M0.5 22 L6.5 0.5 L33.5 0.5 L39.5 22 Z" fill="{t.tab}" />',
            f'<path d="M0.5 22 L6.5 0.5 L33.5 0.5" fill="none" stroke="{hl}" stroke-width="1"/>',
            f'<path d="M33.5 0.5 L39.5 22" fill="none" stroke="{sh}" stroke-width="1"/>']),
        "tab_on": svg(40, 24, [
            f'<path d="M0.5 24 L6.5 0.5 L33.5 0.5 L39.5 24 Z" fill="{t.bg}" />',
            f'<path d="M0.5 24 L6.5 0.5 L33.5 0.5" fill="none" stroke="{hl}" stroke-width="1.4"/>',
            f'<path d="M33.5 0.5 L39.5 24" fill="none" stroke="{sh}" stroke-width="1.4"/>']),
    }


def cache_root() -> str:
    """Where generated pieces live: next to the thumbnail cache by default."""
    root = os.environ.get("ILP_GALLERY_SGI_CACHE")
    if root:
        return root
    return os.path.join(os.path.expanduser("~"), ".cache", "ilp_gallery", "sgi")


def write(t) -> dict[str, str]:
    """Write the theme's pieces (once) and return {name: path} with forward slashes,
    ready for ``url()`` in a stylesheet."""
    files = pieces(t)
    key = hashlib.sha1((str(ASSET_VERSION) + repr(sorted(files.items()))).encode()).hexdigest()[:12]
    for root in (cache_root(), os.path.join(tempfile.gettempdir(), "ilp_gallery_sgi")):
        folder = os.path.join(root, f"{t.name}-{key}")
        try:
            os.makedirs(folder, exist_ok=True)
            out = {}
            for name, text in files.items():
                path = os.path.join(folder, name + ".svg")
                if not os.path.exists(path):
                    tmp = f"{path}.{os.getpid()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(text)
                    os.replace(tmp, path)          # atomic: safe with several sessions
                out[name] = path.replace(os.sep, "/")
            return out
        except OSError:
            continue                               # e.g. read-only home: fall back to the temp dir
    raise OSError("ilp_gallery: nowhere writable for the SGI theme's images")
