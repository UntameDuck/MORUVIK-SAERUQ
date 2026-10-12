import json
import subprocess
import sys


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "saeruq", *args], capture_output=True, text=True, encoding="utf-8")


def test_cli_generate_and_verify(tmp_path):
    completed = run_cli("generate", "--backend", "mock", "--prompt", "한글 프롬프트", "--width", "64", "--height", "64", "--runs", str(tmp_path))
    assert completed.returncode == 0, completed.stderr
    record = json.loads(completed.stdout)
    assert record["request"]["prompt"] == "한글 프롬프트"
    verified = run_cli("verify-run", record["directory"])
    assert verified.returncode == 0
    assert json.loads(verified.stdout)["is_ai"] is False


def test_backend_must_be_explicit():
    assert run_cli("generate", "--prompt", "test").returncode == 2


def test_flux_requires_local_model_path():
    completed = run_cli("generate", "--backend", "flux", "--prompt", "test")
    assert completed.returncode == 2
    assert "model-path" in json.loads(completed.stderr)["message"]


def test_mock_rejects_gpu_settings():
    completed = run_cli("generate", "--backend", "mock", "--prompt", "test", "--cpu-offload")
    assert completed.returncode == 2


def test_doctor_does_not_import_gpu_dependencies():
    completed = run_cli("doctor")
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["gpu"]["checked"] is False
