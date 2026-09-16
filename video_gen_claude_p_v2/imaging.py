"""Deterministic image compositing: arranges several already-generated
images into a single labeled grid. Used by executor.py for all three of a
scene cut's fixed reference groups (see schema.py's SceneCutPlan):
  - one grid per character (face/body + clothing + hair panels) that becomes
    that character's own asset image;
  - one "person grid" per cut combining every on-screen character's asset
    image (make_person_grid) -- the cut's single person-reference slot no
    matter how many characters are in it;
  - one "prop grid" per cut combining every prop/logo referenced in that cut
    (plain make_grid) -- the cut's single prop-reference slot no matter how
    many props are in it.
The same make_grid also builds the final_video anchor frame's person/prop
groups (see schema.py's FinalVideoPlan).

Cells are labeled with burned-in text, not just laid out silently, because
the label is what lets the planning prompt's edit_prompt address a specific
panel by name (e.g. "the outfit from the panel labeled CLOTHING", or for a
multi-character person grid, "the panel labeled char_taemin") -- see
prompts.py's grid-reading rules. Without a visible label Qwen-Edit has no way
to know a panel's role, and risks rendering the grid layout itself instead of
reading it as a reference sheet.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_CELL_SIZE = (512, 512)
_LABEL_HEIGHT = 64
_BG = (24, 24, 28)
_LABEL_BG = (255, 255, 255)
_LABEL_FG = (0, 0, 0)
_MAX_COLS = 4


def _label_font() -> ImageFont.ImageFont:
    # load_default()'s size kwarg needs Pillow >=10.1; the bitmap fallback
    # (~10px) is nearly unreadable at this cell size, and a readable label
    # is the whole point -- see this module's docstring.
    try:
        return ImageFont.load_default(size=36)
    except TypeError:
        return ImageFont.load_default()


def make_grid(items: list[tuple[Path, str]], dest: Path, max_cols: int = _MAX_COLS) -> None:
    """items: [(image_path, label), ...], laid out left-to-right and wrapped
    into rows of at most `max_cols` cells. A character's own 3-panel grid (3
    items) or the anchor frame's groups fit in one row; a cut's prop grid can
    have arbitrarily many props/logos and wraps into further rows."""
    if not items:
        raise ValueError("make_grid()에는 최소 1개의 이미지가 필요합니다.")

    cell_w, cell_h = _CELL_SIZE
    n_cols = min(len(items), max_cols)
    n_rows = math.ceil(len(items) / n_cols)
    row_h = cell_h + _LABEL_HEIGHT
    canvas = Image.new("RGB", (cell_w * n_cols, row_h * n_rows), _BG)
    draw = ImageDraw.Draw(canvas)
    font = _label_font()

    for i, (path, label) in enumerate(items):
        col, row = i % n_cols, i // n_cols
        img = Image.open(path).convert("RGB")
        img.thumbnail((cell_w, cell_h))
        x0 = col * cell_w
        y0 = row * row_h
        paste_x = x0 + (cell_w - img.width) // 2
        paste_y = y0 + (cell_h - img.height) // 2
        canvas.paste(img, (paste_x, paste_y))

        draw.rectangle([x0, y0 + cell_h, x0 + cell_w, y0 + row_h], fill=_LABEL_BG)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            (x0 + (cell_w - tw) / 2, y0 + cell_h + (_LABEL_HEIGHT - th) / 2 - bbox[1]),
            label, fill=_LABEL_FG, font=font,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest)


def make_person_grid(characters: list[tuple[str, Path, Path, Path]], dest: Path) -> Path:
    """characters: [(entity_id, face_body_path, clothing_path, hair_path), ...]
    for every character on screen in one cut/anchor frame.

    Builds the grid from each character's individual panels rather than
    nesting each character's own pre-built 3-panel grid into a new outer
    grid: nesting would downscale an already-labeled 1536x576 image into a
    single ~512x512 cell (thumbnail() shrinks it to ~1/3), shrinking the
    64px label band to ~21px and making it unreadable -- defeating the
    labels' whole purpose (see this module's docstring). Instead, every
    character contributes its own row of 3 full-resolution panels, labeled
    "FACE/BODY"/"CLOTHING"/"HAIR" when there's only one character (nothing
    to disambiguate), or "<entity_id> FACE/BODY" etc. when there are
    several -- prompts.py's grid-reading rules describe exactly this
    layout, and it's what `edit_prompt` must reference."""
    if not characters:
        raise ValueError("make_person_grid()에는 최소 1명의 인물이 필요합니다.")

    if len(characters) == 1:
        _eid, face_body, clothing, hair = characters[0]
        items = [(face_body, "FACE/BODY"), (clothing, "CLOTHING"), (hair, "HAIR")]
    else:
        items = []
        for eid, face_body, clothing, hair in characters:
            items += [
                (face_body, f"{eid} FACE/BODY"),
                (clothing, f"{eid} CLOTHING"),
                (hair, f"{eid} HAIR"),
            ]
    make_grid(items, dest, max_cols=3)
    return dest
