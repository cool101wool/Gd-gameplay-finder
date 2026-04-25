from flask import Flask, send_file
from PIL import Image, ImageDraw
import io, random, os

app = Flask(__name__)

@app.route("/")
def home():
    # Image size
    width, height = 400, 400

    # Create blank white image
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Square size
    square_size = 50

    # Random top-left corner
    x = random.randint(0, width - square_size)
    y = random.randint(0, height - square_size)

    # Draw square (black)
    draw.rectangle([x, y, x + square_size, y + square_size], outline="black", width=3)

    # Save to memory buffer
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return send_file(buf, mimetype="image/png")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
