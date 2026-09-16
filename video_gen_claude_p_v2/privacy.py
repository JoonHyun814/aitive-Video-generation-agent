"""Privacy pass for images pulled in from the open web during planning
(schema.py's *_reference_url fields): blurs faces and/or readable text before
the image is used as a generation reference, so an uninvolved real person's
identity or unrelated text never ends up grounding the ad.

Unlike video_gen_claude_p (v1)'s privacy.py -- which skipped blur entirely for
is_product/is_logo entities -- this module splits face and text blurring into
two independent passes. The executor always runs blur_faces() on every
downloaded reference; it only skips blur_text() for is_product/is_logo
entities (their own printed text/mark needs to stay intact and there's no
uninvolved third party in a product shot or a logo -- but a face is still
blurred if one somehow appears in such a shot).

Both detectors are classical/local (no network call, no extra service):
OpenCV's bundled Haar cascade for frontal faces, and easyocr for text. Both
are best-effort, not a guarantee -- a profile face or an off-cascade angle
passes through unblurred.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter

_FACE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"

# Loading easyocr's detection+recognition models costs real seconds; doing
# that once per process instead of once per image is the whole point of a
# module-level singleton here.
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en", "ko"], gpu=False)
    return _reader


def _blur_region(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    x0, y0 = max(int(x0), 0), max(int(y0), 0)
    x1, y1 = min(int(x1), img.width), min(int(y1), img.height)
    if x1 <= x0 or y1 <= y0:
        return
    region = img.crop((x0, y0, x1, y1))
    radius = max(x1 - x0, y1 - y0) / 6 + 4
    img.paste(region.filter(ImageFilter.GaussianBlur(radius=radius)), (x0, y0))


def blur_faces(path: Path) -> None:
    """Blurs frontal faces in `path` in place. Raises on any detector/image
    failure -- the caller (executor.py) must treat that as "this reference
    can't be trusted" and discard it (falling back to Flux generation) rather
    than risking an unblurred face slipping through."""
    img = Image.open(path).convert("RGB")
    cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

    cascade = cv2.CascadeClassifier(_FACE_CASCADE_PATH)
    if cascade.empty():
        raise RuntimeError("Haar cascade 얼굴 검출기를 불러오지 못했습니다.")
    for x, y, w, h in cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24)):
        _blur_region(img, (x, y, x + w, y + h))

    img.save(path)


def blur_text(path: Path) -> None:
    """Blurs readable text in `path` in place. Same failure contract as
    blur_faces(). Skipped by the executor for is_product/is_logo entities --
    see this module's docstring."""
    img = Image.open(path).convert("RGB")
    # Pass the already-decoded array, not str(path): easyocr's own path-input
    # branch shells out to cv2.imread, which silently returns None (raising
    # an unrelated AttributeError downstream) for a path containing non-ASCII
    # characters on Windows -- e.g. every path under this project's Korean
    # product-name output directories. Feeding the array we already decoded
    # via PIL sidesteps that read entirely.
    cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    for bbox, _text, _conf in _get_reader().readtext(cv_img):
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        _blur_region(img, (min(xs), min(ys), max(xs), max(ys)))

    img.save(path)


def blur_faces_and_text(path: Path, *, skip_text: bool = False) -> None:
    """Convenience wrapper matching the common executor.py call shape: always
    blur_faces(), and blur_text() too unless `skip_text` (is_product/
    is_logo)."""
    blur_faces(path)
    if not skip_text:
        blur_text(path)
