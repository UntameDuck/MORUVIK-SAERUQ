from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Protocol

from PIL import Image


class CancelledError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError("작업이 취소되었습니다.")

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class Request:
    prompt: str
    seed: int = 0
    width: int = 768
    height: int = 768
    source: Path | None = None
    mask: Path | None = None
    edit_mode: str = "full"
    context: int = 64

    def validate(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 10000:
            raise ValueError("프롬프트는 1~10000자여야 합니다.")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed < 2**63:
            raise ValueError("시드는 0 이상 2^63 미만의 정수여야 합니다.")
        if any(isinstance(n, bool) or not isinstance(n, int) or n < 64 or n > 2048 or n % 16 for n in (self.width, self.height)):
            raise ValueError("해상도는 64~2048 범위의 16 배수여야 합니다.")
        if self.edit_mode not in {"full", "crop"}:
            raise ValueError("편집 경로는 full 또는 crop이어야 합니다.")
        if isinstance(self.context, bool) or not isinstance(self.context, int) or not 0 <= self.context <= 2048:
            raise ValueError("주변 문맥 크기는 0~2048의 정수여야 합니다.")
        if self.mask and not self.source:
            raise ValueError("마스크에는 원본 이미지가 필요합니다.")
        if self.edit_mode == "crop" and not self.mask:
            raise ValueError("crop 편집에는 마스크가 필요합니다.")
        for path in (self.source, self.mask):
            if path and not Path(path).is_file():
                raise ValueError(f"입력 파일을 찾을 수 없습니다: {path}")


Progress = Callable[[float], None]


class Backend(Protocol):
    def describe(self) -> dict: ...
    def load(self) -> None: ...
    def infer(self, request: Request, image: Image.Image | None, token: CancellationToken, progress: Progress) -> Image.Image: ...
    def metrics(self) -> dict: ...
    def unload(self) -> None: ...
