from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_contract_keeps_offline_runtime_self_contained():
    dockerfile = ROOT / "Dockerfile"
    dockerignore = ROOT / ".dockerignore"

    assert dockerfile.exists(), "Dockerfile is required for the competition image handoff"
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY app" in text
    assert "COPY public" in text
    assert "COPY data" in text
    assert "HOST=0.0.0.0" in text
    assert 'CMD ["python", "server.py"]' in text

    assert dockerignore.exists(), ".dockerignore should keep local noise out of the image context"
    ignored = dockerignore.read_text(encoding="utf-8")
    assert ".git" in ignored
    assert "test-results" in ignored
    assert "data/*.csv" not in ignored
    assert "data/*.json" not in ignored


def main():
    test_docker_image_contract_keeps_offline_runtime_self_contained()
    print("ok: docker contract")


if __name__ == "__main__":
    main()
