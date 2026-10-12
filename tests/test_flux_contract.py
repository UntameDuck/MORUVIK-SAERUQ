"""Exercise the adapter contract with doubles, never real GPU/model inference."""

import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from saeruq.backends.flux import FluxBackend
from saeruq.contracts import CancellationToken, CancelledError, Request
from saeruq.models import MODEL_ID
from saeruq.storage import atomic_json, digest


@pytest.fixture
def flux_doubles(tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    (root / "model_index.json").write_text("{}")
    (root / "weight.safetensors").write_bytes(b"fixture only, not model weights")
    atomic_json(root / "saeruq-model.json", {"schema_version": 1, "model_id": MODEL_ID, "revision": "a"*40,
                "files": {name: digest(root/name) for name in ("model_index.json", "weight.safetensors")}})
    class Pipeline:
        def __init__(self):
            self.to = Mock()
            self.enable_model_cpu_offload = Mock()
            self.load_lora_weights = Mock()
            self.arguments = None
        def __call__(self, **kwargs):
            self.arguments = kwargs
            for index in range(4):
                kwargs["callback_on_step_end"](self, index, 0, {"latents": "fixture"})
            return SimpleNamespace(images=[Image.new("RGB", (kwargs["width"], kwargs["height"]))])
    pipeline = Pipeline()
    factory = Mock(return_value=pipeline)
    cuda = SimpleNamespace(is_available=Mock(return_value=True), is_bf16_supported=Mock(return_value=True),
                           reset_peak_memory_stats=Mock(), synchronize=Mock(), empty_cache=Mock(),
                           get_device_name=Mock(return_value="test double, not a GPU"),
                           max_memory_allocated=Mock(return_value=100), max_memory_reserved=Mock(return_value=200),
                           memory_allocated=Mock(return_value=50), memory_reserved=Mock(return_value=150))
    generator = SimpleNamespace(manual_seed=Mock(return_value="seeded-generator"))
    torch = SimpleNamespace(cuda=cuda, bfloat16="bf16", version=SimpleNamespace(cuda="test"),
                            inference_mode=nullcontext, Generator=Mock(return_value=generator))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "diffusers", SimpleNamespace(Flux2KleinPipeline=SimpleNamespace(from_pretrained=factory)))
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "0")
    return root, torch, pipeline, factory


def test_loading_uses_local_files_and_safetensors(flux_doubles, monkeypatch):
    root, torch, pipeline, factory = flux_doubles
    backend = FluxBackend(root)
    backend.load()
    factory.assert_called_once_with(str(root), torch_dtype="bf16", local_files_only=True, use_safetensors=True)
    import os
    assert os.environ["HF_HUB_OFFLINE"] == os.environ["TRANSFORMERS_OFFLINE"] == "1"
    pipeline.to.assert_called_once_with("cuda")
    backend.load()
    assert factory.call_count == 1
    assert backend.describe()["loaded"]
    assert backend.metrics()["peak_vram_allocated_bytes"] == 100


def test_failed_device_transfer_does_not_leave_a_loaded_pipeline(flux_doubles):
    root, torch, pipeline, factory = flux_doubles
    pipeline.to.side_effect = RuntimeError("simulated CUDA failure")
    backend = FluxBackend(root)
    with pytest.raises(RuntimeError, match="CUDA failure"):
        backend.load()
    assert backend.pipeline is None
    assert not backend.describe()["loaded"]
    assert torch.cuda.empty_cache.called
    pipeline.to.side_effect = None
    backend.load()
    assert factory.call_count == 2


def test_local_lora_and_cpu_offload_are_explicit(flux_doubles, tmp_path):
    root, torch, pipeline, factory = flux_doubles
    lora = tmp_path / "adapter.safetensors"
    lora.write_bytes(b"fixture adapter")
    backend = FluxBackend(root, offload=True, lora=lora)
    backend.load()
    pipeline.load_lora_weights.assert_called_once_with(str(lora.parent), weight_name=lora.name, local_files_only=True)
    pipeline.enable_model_cpu_offload.assert_called_once()
    pipeline.to.assert_not_called()
    assert backend.describe()["lora_sha256"] == digest(lora)


def test_inference_uses_distilled_settings_and_progress(flux_doubles):
    root, torch, pipeline, factory = flux_doubles
    backend = FluxBackend(root)
    backend.load()
    reference = Image.new("RGB", (64, 64))
    values = []
    output = backend.infer(Request("edit", seed=42, width=64, height=64), reference, CancellationToken(), values.append)
    assert output.size == (64, 64)
    assert values == [.25, .5, .75, 1]
    assert pipeline.arguments["image"] is reference
    assert pipeline.arguments["num_inference_steps"] == 4
    assert pipeline.arguments["guidance_scale"] == 1
    torch.Generator.assert_called_once_with(device="cuda")


def test_inference_stops_at_a_callback_boundary(flux_doubles):
    root, torch, pipeline, factory = flux_doubles
    backend = FluxBackend(root)
    backend.load()
    token = CancellationToken()
    def cancel_after_first(_value):
        token.cancel()
    with pytest.raises(CancelledError):
        backend.infer(Request("test", width=64, height=64), None, token, cancel_after_first)


@pytest.mark.parametrize("capability", ["is_available", "is_bf16_supported"])
def test_unsupported_cuda_never_constructs_pipeline(flux_doubles, capability):
    root, torch, pipeline, factory = flux_doubles
    getattr(torch.cuda, capability).return_value = False
    with pytest.raises(RuntimeError):
        FluxBackend(root).load()
    factory.assert_not_called()


def test_invalid_lora_is_rejected_before_model_hashing(tmp_path):
    with pytest.raises(ValueError, match="LoRA"):
        FluxBackend(tmp_path / "missing-model", lora=tmp_path / "missing-adapter.bin").load()
