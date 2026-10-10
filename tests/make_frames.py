"""Synthetic frames for testing, so the pipeline can be exercised before hardware lands.

These are crude on purpose. They are not a substitute for real rooftop photographs and
the abstention behaviour must be retested on a genuinely obscured lens once the device
exists. What they do test is that the code path works and that a dark or blocked frame
is not confidently described.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFilter


def _jpeg(im):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def clear_scene(w=512, h=384):
    """A crude outdoor scene: sky, a building edge, concrete ground, a shaded patch."""
    im = Image.new("RGB", (w, h), (135, 180, 225))
    d = ImageDraw.Draw(im)
    d.rectangle([0, int(h * 0.62), w, h], fill=(150, 148, 143))        # concrete ground
    d.rectangle([0, int(h * 0.20), int(w * 0.28), int(h * 0.62)], fill=(196, 188, 170))  # wall
    d.rectangle([0, int(h * 0.62), int(w * 0.46), h], fill=(96, 95, 92))  # its shadow
    d.ellipse([int(w * 0.72), int(h * 0.06), int(w * 0.86), int(h * 0.20)], fill=(255, 246, 200))
    return _jpeg(im)


def dark_frame(w=512, h=384):
    """Night, or a lens that has been covered. The abstention case."""
    return _jpeg(Image.new("RGB", (w, h), (7, 7, 9)))


def obscured_frame(w=512, h=384):
    """Dust or water on the lens: there is light, but no usable detail."""
    im = clear_scene(w, h)
    im = Image.open(io.BytesIO(im)).filter(ImageFilter.GaussianBlur(radius=22))
    ov = Image.new("RGB", (w, h), (188, 180, 165))
    return _jpeg(Image.blend(im.convert("RGB"), ov, 0.55))


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frames")
    os.makedirs(out, exist_ok=True)
    for name, fn in (("clear", clear_scene), ("dark", dark_frame), ("obscured", obscured_frame)):
        path = os.path.join(out, name + ".jpg")
        with open(path, "wb") as fh:
            fh.write(fn())
        print("wrote {} ({} bytes)".format(path, os.path.getsize(path)))
