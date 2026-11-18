"""The voice container's place in compose.yml: no network, no env file, read-only root, the models mounted read-only, and only the
socket folder shared with the server; plus the compose level the change needs. (Read as text: no YAML parser in the test set.)"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def compose_text() -> str:
    return (ROOT / "compose.yml").read_text(encoding="utf-8")


def voice_block() -> str:
    text = compose_text()
    start = text.index("\n  voice:\n")
    end = text.index("\n  # ---- subscription CLI sidecars", start)
    return text[start:end]


def server_block() -> str:
    text = compose_text()
    start = text.index("\n  server:\n")
    end = text.index("\n  browser:", start) if "\n  browser:" in text[start:] else start + 4000
    return text[start:end]


def test_voice_service_is_sealed_from_the_network_and_the_keys():
    block = voice_block()
    assert "network_mode: none" in block
    assert "env_file" not in block
    assert "read_only: true" in block
    assert 'user: "1006:1051"' in block
    assert "ROLE: voice" in block
    assert "voice-models:/voice-models:ro" in block
    assert "voice-io:/run/voice" in block
    assert "mem_limit: ${VOICE_MEMORY:-2g}" in block
    assert "cap_drop: [ALL]" in block or "<<: *hardening" in block


def test_server_shares_only_the_voice_socket_folder_and_writes_the_models():
    block = server_block()
    assert '"1051"' in block
    assert "voice-io:/run/voice" in block
    assert "voice-models:/voice-models" in block and "voice-models:/voice-models:ro" not in block
    assert "VOICE_SOCKET: /run/voice/voice.sock" in block


def test_volumes_are_declared_and_compose_level_is_3():
    text = compose_text()
    assert re.search(r"^  voice-io: \{\}", text, re.M) and re.search(r"^  voice-models: \{\}", text, re.M)
    assert re.search(r"^x-kelo-compose-level:\s*3\b", text, re.M)


def test_the_locked_vad_model_is_pinned_by_hash():
    lock = (ROOT / "docker" / "voice.lock").read_text(encoding="utf-8")
    assert re.search(r"^SILERO_VAD_SHA256=[0-9a-f]{64}$", lock, re.M)
    assert "/resolve/" not in lock and "/main/" not in lock
    assert "SILERO_VAD_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx" in lock


def test_dockerfile_builds_the_voice_user_and_socket_folder():
    text = (ROOT / "docker" / "server.Dockerfile").read_text(encoding="utf-8")
    assert "useradd -m -u 1006 -g voice-io voice" in text
    assert "chown 1006:1051 /run/voice && chmod 2770 /run/voice" in text
    assert "sha256sum -c -" in text and "/opt/voice/silero_vad.onnx" in text
