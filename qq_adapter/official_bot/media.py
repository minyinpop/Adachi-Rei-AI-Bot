"""Image and animated GIF handling for incoming QQ messages."""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageSequence


def _extract_gif_frames(image_bytes: bytes, frame_limit: int = 4) -> list[bytes] | None:
    """Return representative GIF frames as PNG bytes, or None for non-GIFs."""
    with Image.open(BytesIO(image_bytes)) as image:
        if image.format != "GIF":
            return None

        frame_count = getattr(image, "n_frames", 1)
        if frame_count <= 1:
            selected_indices = [0]
        else:
            durations = []
            for frame in ImageSequence.Iterator(image):
                durations.append(max(1, int(frame.info.get("duration", 100))))
            total_duration = sum(durations)
            targets = [0, total_duration * 0.34, total_duration * 0.67, total_duration * 0.95]
            selected_indices = []
            for target in targets[:frame_limit]:
                elapsed = 0
                selected = frame_count - 1
                for index, duration in enumerate(durations):
                    elapsed += duration
                    if elapsed >= target:
                        selected = index
                        break
                if selected not in selected_indices:
                    selected_indices.append(selected)

        png_frames = []
        for frame_index in selected_indices:
            image.seek(frame_index)
            frame = image.convert("RGBA")
            background = Image.new("RGBA", frame.size, "white")
            background.alpha_composite(frame)
            frame = background.convert("RGB")
            frame.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = BytesIO()
            frame.save(output, format="PNG", optimize=True)
            png_frames.append(output.getvalue())
        return png_frames
