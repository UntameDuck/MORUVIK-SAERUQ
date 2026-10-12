from PIL import Image, ImageChops, ImageOps


def read_image(path) -> Image.Image:
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def read_mask(path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as opened:
        # Explicit grayscale: white edits, black protects. Alpha is not a mask.
        mask = ImageOps.exif_transpose(opened).convert("L")
    if mask.size != size:
        raise ValueError("마스크와 원본 이미지의 크기가 다릅니다.")
    if mask.getbbox() is None:
        raise ValueError("마스크에 편집할 영역이 없습니다.")
    return mask


def crop_box(mask: Image.Image, context: int) -> tuple[int, int, int, int]:
    box = mask.getbbox()
    if box is None:
        raise ValueError("빈 마스크입니다.")
    x0, y0, x1, y1 = box
    return max(0, x0-context), max(0, y0-context), min(mask.width, x1+context), min(mask.height, y1+context)


def composite(original: Image.Image, edited: Image.Image, mask: Image.Image) -> Image.Image:
    if not original.size == edited.size == mask.size:
        raise ValueError("합성 이미지 크기가 일치하지 않습니다.")
    return Image.composite(edited.convert("RGB"), original.convert("RGB"), mask)


def outside_changed(original: Image.Image, result: Image.Image, mask: Image.Image) -> int:
    difference = ImageChops.difference(original.convert("RGB"), result.convert("RGB"))
    pixels = difference.tobytes()
    return sum(1 for i, alpha in enumerate(mask.tobytes()) if alpha == 0 and any(pixels[3*i:3*i+3]))
