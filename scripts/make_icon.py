from PIL import Image
img = Image.open(r"F:\comfyui\output\open-brain-concept-art.png")
img = img.convert("RGBA")
# Crop to square from center
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
img = img.crop((left, top, left + side, top + side))
# Save as multi-size ico
img.save(
    r"F:\open-brain\assets\brain.ico",
    format="ICO",
    sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]
)
print("Done: F:\\open-brain\\assets\\brain.ico")
