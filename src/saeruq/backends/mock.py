import hashlib
import random
from PIL import Image, ImageDraw

from saeruq.contracts import CancellationToken, Progress, Request


class MockBackend:
    """Deterministic fixtures. These are not AI-generated images."""

    def describe(self) -> dict:
        return {"id": "mock", "is_ai": False, "model": None, "capabilities": {"generate": True, "reference_edit": True, "native_mask_edit": False}, "label": "개발용 샘플 · AI 추론 아님"}

    def load(self) -> None:
        pass

    def infer(self, request: Request, image: Image.Image | None, token: CancellationToken, progress: Progress) -> Image.Image:
        seed = int.from_bytes(hashlib.sha256(f"{request.seed}:{request.prompt}".encode()).digest()[:8], "big")
        rng = random.Random(seed)
        output = image.resize((request.width, request.height)).convert("RGB") if image else Image.new("RGB", (request.width, request.height), "#e7e1d2")
        draw = ImageDraw.Draw(output)
        for index in range(12):
            token.check()
            x, y = rng.randrange(request.width), rng.randrange(request.height)
            radius = rng.randrange(10, max(11, min(request.width, request.height)//3))
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=tuple(rng.randrange(35, 225) for _ in range(3)))
            progress((index+1)/12)
        draw.rectangle((4, 4, min(request.width-1, 180), 24), fill="white")
        draw.text((8, 8), "SAERUQ MOCK / NOT AI", fill="black")
        return output

    def metrics(self) -> dict:
        return {"peak_vram_allocated_bytes": None, "peak_vram_reserved_bytes": None}

    def unload(self) -> None:
        pass
