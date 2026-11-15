---
name: Images
description: Look at photos, generate pictures, keep product photos, and edit or convert images.
---

# Images

- A photo the user uploaded: `view_image(name)`. If your model cannot see images, say so instead of guessing what it shows.
- A new picture: `generate_image` with a clear prompt (subject, style, setting, light, format). It uses the image service the user connected and costs money on their account; if none is connected, offer `add_service_key`.
- A product photo from a shop page: `save_product_image(url)` with the image's own https address; `crop_top_percent` removes the top part (for example a model's face). `list_product_images` shows what was kept.
- Resizing, cropping, combining, converting formats or making a GIF: write code for `workroom_run` (ImageMagick `convert`, Pillow, ffmpeg). Results land in the user's files.
- Never present a generated picture as a real photo of a real person or event.
