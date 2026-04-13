from PIL import Image, ImageFilter, ImageDraw
import os

input_path = r"C:\Users\Marc\.gemini\antigravity\brain\22267d9a-7789-4989-940f-9b56be2145a3\epic_marketing_office_1774108947023.png"
output_path = r"C:\Users\Marc\Desktop\Gen Z autamation\resized_office.png"

# Open original image
img = Image.open(input_path)

# --- 1. Create the sharp top image (1000x600) ---
target_width = 1000
target_height = 600

img_ratio = img.width / img.height
target_ratio = target_width / target_height

if img_ratio > target_ratio:
    new_width = int(target_ratio * img.height)
    left = (img.width - new_width) / 2
    top = 0
    right = (img.width + new_width) / 2
    bottom = img.height
    img_sharp = img.crop((left, top, right, bottom))
else:
    new_height = int(img.width / target_ratio)
    left = 0
    top = (img.height - new_height) / 2
    right = img.width
    bottom = (img.height + new_height) / 2
    img_sharp = img.crop((left, top, right, bottom))

img_sharp = img_sharp.resize((target_width, target_height), Image.Resampling.LANCZOS)

# --- 2. Create the full blurred background (1000x1000) ---
bg_ratio = 1.0
if img_ratio > bg_ratio:
    new_width = img.height
    left = (img.width - new_width) / 2
    top = 0
    right = (img.width + new_width) / 2
    bottom = img.height
    img_bg = img.crop((left, top, right, bottom))
else:
    new_height = img.width
    left = 0
    top = (img.height - new_height) / 2
    right = img.width
    bottom = (img.height + new_height) / 2
    img_bg = img.crop((left, top, right, bottom))

img_bg = img_bg.resize((1000, 1000), Image.Resampling.LANCZOS)
# Apply a heavy blur
img_bg = img_bg.filter(ImageFilter.GaussianBlur(radius=25))

# Darken the blurred background so white text stands out (adds the "black" element)
dark_overlay = Image.new("RGBA", (1000, 1000), (0, 0, 0, 170)) # Semi-transparent black
img_bg = img_bg.convert("RGBA")
img_bg = Image.alpha_composite(img_bg, dark_overlay)

# --- 3. Composite everything together ---
canvas = Image.new("RGBA", (1000, 1000))
canvas.paste(img_bg, (0, 0))
canvas.paste(img_sharp, (0, 0))

# Smooth transition line (gradient fade) to make it look professional
# We draw a gradient shadow from y=500 down to y=600 to blend the sharp into the blurred
shadow = Image.new("RGBA", (1000, 1000), (0,0,0,0))
draw = ImageDraw.Draw(shadow)
for y in range(500, 600):
    opacity = int(255 * (y - 500) / 100) # Fades from 0 to 255
    draw.line([(0, y), (1000, y)], fill=(0, 0, 0, opacity))

canvas = Image.alpha_composite(canvas, shadow)

# Save
canvas = canvas.convert("RGB")
canvas.save(output_path)
print(f"Saved to {output_path}")
