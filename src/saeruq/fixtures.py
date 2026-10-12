from pathlib import Path
from PIL import Image, ImageDraw

from saeruq.storage import atomic_json


def make_fixtures(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (768, 768), "#e8e3d7")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 510, 768, 768), fill="#adbdad")
    draw.rounded_rectangle((280, 240, 480, 540), radius=28, fill="#d98357", outline="#513a34", width=4)
    draw.ellipse((310, 190, 450, 270), fill="#f2cf9a", outline="#513a34", width=4)
    draw.text((24, 24), "SAERUQ evaluation fixture / synthetic shapes", fill="#302c29")
    image.save(root / "source.png")
    mask = Image.new("L", image.size)
    ImageDraw.Draw(mask).rectangle((270, 180, 490, 550), fill=255)
    mask.save(root / "mask.png")
    tasks = []
    prompts = ["A ceramic vase on a wooden table", "숲속의 작은 집, 수채화", "An editorial illustration of a city at dawn", "파란색 로봇 캐릭터, 단순한 배경", "A studio photograph of a glass bottle"]
    for i in range(15):
        tasks.append({"id": f"generate-{i+1:02}", "kind": "generate", "prompt": prompts[i%5], "seed": i, "width": 768 if i < 10 else 1024, "height": 768 if i < 10 else 1024})
    for i in range(10):
        tasks.append({"id": f"reference-{i+1:02}", "kind": "edit", "prompt": "Reinterpret this scene as a watercolor illustration" if i%2 else "이 장면을 연필 드로잉으로 바꿔줘", "source": "source.png", "seed": i, "width": 768, "height": 768})
    for i in range(15):
        tasks.append({"id": f"masked-{i+1:02}", "kind": "edit", "prompt": "Change the orange vase to a blue vase, keep the background unchanged" if i%2 else "주황색 꽃병을 초록색으로 바꾸고 배경은 유지해줘", "source": "source.png", "mask": "mask.png", "edit_mode": "full" if i < 8 else "crop", "seed": i, "width": 768, "height": 768})
    # Repeated fixed seeds also exercise independent run persistence and hashes.
    for i in range(10):
        tasks.append({"id": f"repeat-{i+1:02}", "kind": "generate", "prompt": prompts[0], "seed": 42, "width": 768, "height": 768})
    manifest = root / "evaluation.json"
    atomic_json(manifest, {"schema_version": 1, "fixture_notice": "Synthetic fixture, not a representative photographic quality benchmark. Replace/add licensed photos for quality decisions.", "tasks": tasks})
    return manifest
