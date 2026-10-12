import gc
import os
from pathlib import Path

from PIL import Image

from saeruq.contracts import CancellationToken, Progress, Request
from saeruq.models import verify_model
from saeruq.storage import digest


class FluxBackend:
    def __init__(self, model_path: Path, offload: bool = False, lora: Path | None = None):
        self.model_path = model_path.resolve()
        self.offload = offload
        self.lora = lora.resolve() if lora else None
        self.pipeline = None
        self.torch = None
        self.manifest = None

    def describe(self) -> dict:
        return {"id": "flux-klein-4b", "is_ai": True, "model": "black-forest-labs/FLUX.2-klein-4B", "revision": self.manifest["revision"] if self.manifest else None, "precision": "bf16", "steps": 4, "cpu_offload": self.offload, "lora_sha256": digest(self.lora) if self.lora and self.lora.is_file() else None, "capabilities": {"generate": True, "reference_edit": True, "native_mask_edit": False, "quantization": False}}

    def load(self) -> None:
        if self.pipeline is not None:
            return
        # No remote fallback. Preparation is a separate explicitly online command.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        self.manifest = verify_model(self.model_path)
        import torch
        from diffusers import Flux2KleinPipeline
        self.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("NVIDIA CUDA GPU가 필요합니다. 현재 PC에서는 mock backend를 사용하세요.")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("이 초기 어댑터에는 BF16 지원 GPU가 필요합니다.")
        self.pipeline = Flux2KleinPipeline.from_pretrained(str(self.model_path), torch_dtype=torch.bfloat16, local_files_only=True, use_safetensors=True)
        if self.lora:
            if not self.lora.is_file() or self.lora.suffix != ".safetensors":
                raise ValueError("LoRA는 로컬 .safetensors 파일이어야 합니다.")
            self.pipeline.load_lora_weights(str(self.lora.parent), weight_name=self.lora.name, local_files_only=True)
        if self.offload:
            self.pipeline.enable_model_cpu_offload()
        else:
            self.pipeline.to("cuda")

    def infer(self, request: Request, image: Image.Image | None, token: CancellationToken, progress: Progress) -> Image.Image:
        torch = self.torch
        torch.cuda.reset_peak_memory_stats()
        def step(_pipe, index, _timestep, values):
            token.check()
            progress((index+1)/4)
            return values
        token.check()
        with torch.inference_mode():
            output = self.pipeline(prompt=request.prompt, image=image, width=request.width, height=request.height,
                                   num_inference_steps=4, guidance_scale=1.0,
                                   generator=torch.Generator(device="cuda").manual_seed(request.seed),
                                   callback_on_step_end=step).images[0]
        torch.cuda.synchronize()
        token.check()
        return output.convert("RGB")

    def metrics(self) -> dict:
        if self.torch and self.torch.cuda.is_available():
            return {"gpu_name": self.torch.cuda.get_device_name(), "cuda_version": self.torch.version.cuda, "peak_vram_allocated_bytes": self.torch.cuda.max_memory_allocated(), "peak_vram_reserved_bytes": self.torch.cuda.max_memory_reserved()}
        return {}

    def unload(self) -> None:
        self.pipeline = None
        gc.collect()
        if self.torch and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
