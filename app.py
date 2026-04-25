from flask import Flask, send_file, session, redirect, url_for, request, render_template_string
from PIL import Image
import io, random, os, json, base64, uuid

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gd-viewer-secret")

# ── Constants ──────────────────────────────────────────────────────────────────
BG_COLOR  = (0, 0x98, 0xFF, 255)   # #0098FF
# The full canvas rendered server-side (covers the whole level extent)
# We pad 500px on each side so the player can scroll to the edges.
PADDING   = 500

# ── Server-side level store (in-memory, keyed by session id) ──────────────────
# Stores: { sid: { "objects": [...], "meta": {...}, "origin": (x, y) } }
_level_store: dict = {}
  <meta charset="utf-8">
  <title>GD Level Viewer</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #1a1a2e;
      color: #eee;
      font-family: monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px;
      gap: 14px;
    }
    h2 { color: #0af; }

    #viewport {
      width: 900px;
      height: 600px;
      max-width: 100%;
      overflow: hidden;
      border: 3px solid #0af;
      position: relative;
      cursor: grab;
      background: #0098FF;
    }
    #viewport:active { cursor: grabbing; }
    #level-img {
      position: absolute;
      top: 0; left: 0;
      display: block;
      /* transform set by JS to pan */
    }

    .info {
      background: #0d0d1a;
      border: 1px solid #0af;
      padding: 14px 20px;
      border-radius: 6px;
      width: 900px;
      max-width: 100%;
      white-space: pre-wrap;
      line-height: 1.8;
    }
    .label { color: #0af; }
    .hint  { color: #555; font-size: 12px; }
  </style>
</head>
<body>
  <h2>GD Level Viewer</h2>
  <p class="hint">Refresh the page for a new level &nbsp;·&nbsp; WASD or arrow keys to move &nbsp;·&nbsp; click-drag also works</p>

  <div id="viewport">
    <img id="level-img" src="/image" alt="level" draggable="false">
  </div>

  <div class="info">
<span class="label">id:</span>           {{ level_id }}
<span class="label">difficulty:</span>   {{ stars }} stars
<span class="label">name:</span>         {{ name }}
<span class="label">description:</span>  {{ description }}

<span class="hint">Not all gd objects are included in the display</span>
  </div>

<script>
  // Starting scroll position: centre of viewport on the focus point
  // focus_screen_x / focus_screen_y are the pixel coords in the full image
  // where the camera should start.
  const FOCUS_X = {{ focus_x }};
  const FOCUS_Y = {{ focus_y }};
  const VP_W = 900, VP_H = 600;
  const STEP  = 100;   // WASD move distance in pixels

  const viewport = document.getElementById("viewport");
  const img      = document.getElementById("level-img");

  // panX / panY = top-left corner of the image within the viewport
  // (negative means we've scrolled right/down into the image)
  let panX = -(FOCUS_X - VP_W / 2);
  let panY = -(FOCUS_Y - VP_H / 2);

  function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }

  function applyPan() {
    // Once the image is loaded we know its size; before that just apply
    const iw = img.naturalWidth  || img.width  || 9999999;
    const ih = img.naturalHeight || img.height || 9999999;
    panX = clamp(panX, -(iw - VP_W), 0);
    panY = clamp(panY, -(ih - VP_H), 0);
    img.style.transform = `translate(${panX}px, ${panY}px)`;
  }

  img.addEventListener("load", applyPan);
  applyPan();   // also call immediately in case it's cached

  // ── Keyboard ────────────────────────────────────────────────────────────────
  document.addEventListener("keydown", function(e) {
    switch (e.key) {
      case "ArrowLeft":  case "a": case "A": panX += STEP; break;
      case "ArrowRight": case "d": case "D": panX -= STEP; break;
      case "ArrowUp":    case "w": case "W": panY += STEP; break;
      case "ArrowDown":  case "s": case "S": panY -= STEP; break;
      default: return;
    }
    e.preventDefault();
    applyPan();
  });

  // ── Click-drag ───────────────────────────────────────────────────────────────
  let dragging = false, dragStartX, dragStartY, panStartX, panStartY;

  viewport.addEventListener("mousedown", function(e) {
    dragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    panStartX  = panX;
    panStartY  = panY;
  });
  document.addEventListener("mousemove", function(e) {
    if (!dragging) return;
    panX = panStartX + (e.clientX - dragStartX);
    panY = panStartY + (e.clientY - dragStartY);
    applyPan();
  });
  document.addEventListener("mouseup", function() { dragging = false; });
</script>
</body>
</html>
"""

# ── Level helpers ──────────────────────────────────────────────────────────────

def load_levels(path="levels_all.json"):
    with open(path, "r") as f:
        return json.load(f)


def parse_level_data(level_data_str: str) -> list[dict]:
    segments = level_data_str.split(";")
    objects  = []
    for seg in segments[1:]:
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(",")
        obj   = {}
        for i in range(0, len(parts) - 1, 2):
            try:
                obj[int(parts[i])] = parts[i + 1]
            except (ValueError, IndexError):
                continue
        if obj:
            objects.append(obj)
    return objects


def object_to_dict(raw: dict) -> dict:
    return {
        "id":       int(raw.get(1, 1)),
        "x":        float(raw.get(2, 0)),
        "y":        float(raw.get(3, 0)),
        "flip_x":   raw.get(4, "0") == "1",
        "flip_y":   raw.get(5, "0") == "1",
        "rotation": float(raw.get(6, 0)),
    }


def decode_description(b64: str) -> str:
    try:
        padded = b64 + "=" * (-len(b64) % 4)
        return base64.b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return b64


# ── Texture cache ──────────────────────────────────────────────────────────────

_texture_cache: dict[int, "Image.Image | None"] = {}

def get_texture(obj_id: int):
    if obj_id in _texture_cache:
        return _texture_cache[obj_id]
    path = os.path.join("textures", f"{obj_id}.png")
    if os.path.exists(path):
        tex = Image.open(path).convert("RGBA")
        _texture_cache[obj_id] = tex
    else:
        _texture_cache[obj_id] = None
    return _texture_cache[obj_id]


# ── Full-level render ──────────────────────────────────────────────────────────

def render_full_level(objects: list[dict]) -> tuple[Image.Image, int, int]:
    """
    Render every object into one big canvas sized to fit all objects + PADDING.
    Returns (image, origin_x, origin_y) where origin is the pixel coordinate
    in the image that corresponds to world position (0, 0).
    GD y-axis is upward; screen y-axis is downward.
    """
    if not objects:
        img = Image.new("RGBA", (900, 600), BG_COLOR)
        return img, 0, 0

    xs = [o["x"] for o in objects]
    ys = [o["y"] for o in objects]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Canvas size in pixels
    canvas_w = int(max_x - min_x) + PADDING * 2 + 128   # +128 for sprite width
    canvas_h = int(max_y - min_y) + PADDING * 2 + 128

    # Pixel coordinate of world (0, 0)
    origin_x = int(-min_x) + PADDING
    origin_y = int(max_y)  + PADDING   # flipped

    canvas = Image.new("RGBA", (canvas_w, canvas_h), BG_COLOR)

    for obj in objects:
        tex = get_texture(obj["id"])
        if tex is None:
            continue

        sprite = tex.copy()
        if obj["flip_x"]:
            sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
        if obj["flip_y"]:
            sprite = sprite.transpose(Image.FLIP_TOP_BOTTOM)
        if obj["rotation"] != 0:
            sprite = sprite.rotate(-obj["rotation"], expand=True)

        sw, sh = sprite.size
        # World → canvas pixel (y inverted)
        px = origin_x + int(obj["x"]) - sw // 2
        py = origin_y - int(obj["y"]) - sh // 2

        # Clip to canvas bounds
        if px + sw < 0 or px > canvas_w:
            continue
        if py + sh < 0 or py > canvas_h:
            continue

        canvas.alpha_composite(sprite, dest=(px, py))

    # ── Darken everything below world y = 0 ───────────────────────────────────
    # origin_y is the canvas row that corresponds to world y = 0.
    # Rows with index > origin_y are below ground (GD y is upward).
    if origin_y < canvas_h:
        import numpy as np
        arr = np.array(canvas, dtype=np.float32)          # H×W×4  (RGBA)
        # Multiply RGB channels by 0.5 for all rows below the ground line.
        # Alpha channel is left untouched.
        arr[origin_y:, :, :3] *= 0.5
        arr = arr.clip(0, 255).astype(np.uint8)
        canvas = Image.fromarray(arr, "RGBA")

    return canvas, origin_x, origin_y


def flatten_to_png(img: Image.Image) -> io.BytesIO:
    final = Image.new("RGB", img.size, (255, 255, 255))
    final.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    final.save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf


# ── Session / store helpers ────────────────────────────────────────────────────

def get_sid() -> str:
    """Return existing session id or create a new one."""
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


def init_level():
    sid    = get_sid()
    levels = load_levels()
    level  = random.choice(levels)
    meta   = level.get("metadata", {})

    raw_objs = parse_level_data(level["level_data"])
    objects  = [object_to_dict(o) for o in raw_objs]
    focus    = random.choice(objects) if objects else {"x": 0.0, "y": 0.0}

    _level_store[sid] = {
        "objects":     objects,
        "focus":       focus,
        "level_id":    level.get("id", "?"),
        "level_name":  meta.get("name", "Unknown"),
        "level_stars": meta.get("stars", "?"),
        "level_desc":  decode_description(meta.get("description", "")),
    }


def get_store() -> dict | None:
    return _level_store.get(session.get("sid"))


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    # Always generate a fresh level on page load (refresh = new level)
    init_level()
    store = get_store()

    # Compute where in the full image the focus object will land
    # We need the bounding box info — easiest to do a dry-run of the origin calc
    objects = store["objects"]
    focus   = store["focus"]

    if objects:
        xs     = [o["x"] for o in objects]
        ys     = [o["y"] for o in objects]
        min_x  = min(xs)
        max_y  = max(ys)
        origin_x = int(-min_x) + PADDING
        origin_y = int(max_y)  + PADDING
        focus_screen_x = origin_x + int(focus["x"])
        focus_screen_y = origin_y - int(focus["y"])
    else:
        focus_screen_x = 450
        focus_screen_y = 300

    return render_template_string(
        HTML,
        level_id    = store["level_id"],
        name        = store["level_name"],
        stars       = store["level_stars"],
        description = store["level_desc"],
        focus_x     = focus_screen_x,
        focus_y     = focus_screen_y,
    )


@app.route("/image")
def image():
    store = get_store()
    if store is None:
        # Fallback blank image
        img = Image.new("RGBA", (900, 600), BG_COLOR)
        buf = flatten_to_png(img)
        return send_file(buf, mimetype="image/png")

    img, _, _ = render_full_level(store["objects"])
    buf = flatten_to_png(img)
    return send_file(buf, mimetype="image/png")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)