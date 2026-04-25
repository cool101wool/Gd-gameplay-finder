from flask import Flask, send_file
from PIL import Image, ImageDraw, ImageFont
import io, random, os, json, base64
import numpy as np

app = Flask(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
IMG_W, IMG_H  = 900, 600
BG_COLOR      = (0, 0x98, 0xFF)          # #0098FF  (RGB)
GROUND_DARKEN = 0.5                       # factor applied below y=0
TEXT_H        = 60                        # pixel rows reserved at top for info
LEVELS_PATH   = "levels_all.json"

# ── Level loading ──────────────────────────────────────────────────────────────

def load_levels():
    with open(LEVELS_PATH) as f:
        return json.load(f)


def parse_objects(level_data_str: str) -> list[dict]:
    """Parse GD level string → list of object dicts."""
    result = []
    for seg in level_data_str.split(";")[1:]:   # skip colour header
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(",")
        obj = {}
        for i in range(0, len(parts) - 1, 2):
            try:
                obj[int(parts[i])] = parts[i + 1]
            except (ValueError, IndexError):
                pass
        if obj:
            result.append({
                "id":       int(obj.get(1, 1)),
                "x":        float(obj.get(2, 0)),
                "y":        float(obj.get(3, 0)),
                "flip_x":   obj.get(4) == "1",
                "flip_y":   obj.get(5) == "1",
                "rotation": float(obj.get(6, 0)),
            })
    return result


def decode_b64(s: str) -> str:
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return s

# ── Texture cache ──────────────────────────────────────────────────────────────

_tex_cache: dict[int, np.ndarray | None] = {}

def get_texture(obj_id: int) -> np.ndarray | None:
    """Return RGBA uint8 numpy array for the given object id, or None."""
    if obj_id in _tex_cache:
        return _tex_cache[obj_id]
    path = os.path.join("textures", f"{obj_id}.png")
    if os.path.exists(path):
        tex = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
    else:
        tex = None
    _tex_cache[obj_id] = tex
    return tex

# ── Rendering ──────────────────────────────────────────────────────────────────

def alpha_composite_onto(canvas: np.ndarray, sprite: np.ndarray, px: int, py: int):
    """
    Premultiplied-alpha composite sprite onto canvas (both RGBA uint8 numpy arrays).
    Clips sprite to canvas bounds automatically.
    """
    ch, cw = canvas.shape[:2]
    sh, sw = sprite.shape[:2]

    # Destination rect in canvas space
    x0c = max(px, 0);  y0c = max(py, 0)
    x1c = min(px + sw, cw); y1c = min(py + sh, ch)
    if x0c >= x1c or y0c >= y1c:
        return

    # Corresponding rect in sprite space
    x0s = x0c - px;  y0s = y0c - py
    x1s = x0s + (x1c - x0c); y1s = y0s + (y1c - y0c)

    dst = canvas[y0c:y1c, x0c:x1c].astype(np.float32)
    src = sprite[y0s:y1s, x0s:x1s].astype(np.float32)

    sa = src[..., 3:4] / 255.0
    da = dst[..., 3:4] / 255.0
    out_a = sa + da * (1.0 - sa)

    with np.errstate(divide="ignore", invalid="ignore"):
        out_rgb = np.where(
            out_a > 0,
            (src[..., :3] * sa + dst[..., :3] * da * (1.0 - sa)) / out_a,
            0.0,
        )

    result = np.empty_like(dst)
    result[..., :3] = out_rgb
    result[..., 3:]  = out_a * 255.0
    canvas[y0c:y1c, x0c:x1c] = result.clip(0, 255).astype(np.uint8)


def transform_sprite(tex: np.ndarray, flip_x: bool, flip_y: bool, rotation: float) -> np.ndarray:
    img = Image.fromarray(tex)
    if flip_x:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if flip_y:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    if rotation:
        img = img.rotate(-rotation, expand=True)
    return np.array(img)


def render(objects: list[dict], cam_x: float, cam_y: float,
           level_id: str, name: str, stars: str, description: str) -> Image.Image:

    render_h = IMG_H - TEXT_H                  # pixel rows for the level view
    half_w   = IMG_W / 2
    half_h   = render_h / 2

    # Build canvas as numpy array (RGBA)
    canvas = np.empty((IMG_H, IMG_W, 4), dtype=np.uint8)
    # Top TEXT_H rows: dark background for text
    canvas[:TEXT_H]  = (20, 20, 40, 255)
    # Level-view rows: sky blue
    canvas[TEXT_H:]  = (*BG_COLOR, 255)

    # ground_row = canvas row that corresponds to world y=0
    ground_row = TEXT_H + int(half_h + cam_y)   # cam_y positive → ground moves down

    # Darken rows below world y=0 in the level area
    dark_start = max(TEXT_H, ground_row)
    if dark_start < IMG_H:
        canvas[dark_start:, :, :3] = (canvas[dark_start:, :, :3].astype(np.float32) * GROUND_DARKEN).astype(np.uint8)

    # Draw objects
    for obj in objects:
        tex = get_texture(obj["id"])
        if tex is None:
            continue

        sprite = transform_sprite(tex, obj["flip_x"], obj["flip_y"], obj["rotation"])
        sh, sw = sprite.shape[:2]

        # World → screen (y inverted, offset into canvas below text bar)
        px = int(obj["x"] - cam_x + half_w - sw / 2)
        py = TEXT_H + int(-(obj["y"] - cam_y) + half_h - sh / 2)

        # Quick cull
        if px + sw <= 0 or px >= IMG_W:   continue
        if py + sh <= TEXT_H or py >= IMG_H: continue

        alpha_composite_onto(canvas, sprite, px, py)

    img = Image.fromarray(canvas, "RGBA")

    # ── Text overlay ───────────────────────────────────────────────────────────
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    line1 = f"id: {level_id}   difficulty: {stars}   name: {name}"
    line2 = f"description: {description}"

    draw.text((8, 6),  line1, fill=(255, 220, 80),  font=font)
    draw.text((8, 28), line2, fill=(200, 200, 200), font=font)

    return img.convert("RGB")

# ── Camera clamping ────────────────────────────────────────────────────────────

def clamped_camera(focus: dict, objects: list[dict]) -> tuple[float, float]:
    """
    Start with the focus object's position, then clamp so the viewport
    doesn't show empty space beyond the level bounds.
    Priority: don't clip left > don't clip bottom > don't clip right > don't clip top.
    """
    if not objects:
        return focus["x"], focus["y"]

    xs = [o["x"] for o in objects]
    ys = [o["y"] for o in objects]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    render_h = IMG_H - TEXT_H
    half_w   = IMG_W / 2
    half_h   = render_h / 2

    cam_x = focus["x"]
    cam_y = focus["y"]

    # Left priority first: cam_x - half_w >= min_x  →  cam_x >= min_x + half_w
    cam_x = max(cam_x, min_x + half_w)
    # Then right: cam_x + half_w <= max_x  →  cam_x <= max_x - half_w
    cam_x = min(cam_x, max_x - half_w)

    # Bottom priority first: cam_y - half_h >= min_y  →  cam_y >= min_y + half_h
    cam_y = max(cam_y, min_y + half_h)
    # Then top: cam_y + half_h <= max_y  →  cam_y <= max_y - half_h
    cam_y = min(cam_y, max_y - half_h)

    return cam_x, cam_y

# ── Flask route ────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    levels = load_levels()
    level  = random.choice(levels)
    meta   = level.get("metadata", {})

    objects     = parse_objects(level["level_data"])
    focus       = random.choice(objects) if objects else {"x": 0.0, "y": 0.0}
    cam_x, cam_y = clamped_camera(focus, objects)

    level_id    = meta.get("id", "?")
    name        = meta.get("name", "Unknown")
    stars       = meta.get("stars", "?")
    description = decode_b64(meta.get("description", ""))

    img = render(objects, cam_x, cam_y, level_id, name, stars, description)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)