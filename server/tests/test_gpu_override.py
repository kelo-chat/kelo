"""The host GPU hook (tools/gpu_override.py, run by scripts/install.sh): a real render node is passed into the two browser services;
no node means no change; a file that is not ours is never touched. The browser entrypoint uses SwiftShader only without a node."""

import importlib.util
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "gpu_override.py"
ENTRYPOINT = (ROOT / "docker" / "entrypoint.sh").read_text()


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("gpu_override", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dri(tmp_path, names):
    dri = Path(tempfile.mkdtemp(dir=tmp_path))
    for n in names:
        (dri / n).write_text("")
    return dri


def test_render_nodes_are_only_the_render_devices_in_numeric_order(tool, tmp_path):
    dri = _dri(tmp_path, ["card0", "renderD129", "renderD128", "by-path", "renderD9"])
    assert tool.render_nodes(str(dri)) == ["renderD9", "renderD128", "renderD129"]


def test_no_folder_or_no_node_gives_no_nodes(tool, tmp_path):
    assert tool.render_nodes(str(tmp_path / "missing")) == []
    assert tool.render_nodes(str(_dri(tmp_path, ["card0"]))) == []


def test_the_override_passes_the_dri_folder_and_render_group_to_both_browsers(tool):
    text = tool.override_text(992)
    for service in ("browser", "work-browser"):
        assert f"  {service}:\n" in text
    assert text.count('"/dev/dri:/dev/dri"') == 2 and text.count('"992"') == 2
    assert "server:" not in text and "image" not in text          # nothing else of the stack is touched


def test_with_a_node_the_file_is_written_with_the_node_group(tool, tmp_path, monkeypatch):
    dri = _dri(tmp_path, ["renderD128"])
    project = tmp_path / "kelo"
    project.mkdir()
    assert tool.apply(project, str(dri), gid_of=lambda path: 44) == "written"
    text = (project / tool.OVERRIDE_NAME).read_text()
    assert text.startswith(tool.MARKER) and '"44"' in text
    assert tool.apply(project, str(dri), gid_of=lambda path: 44) == "unchanged"


def test_without_a_node_nothing_is_written_and_our_file_is_removed(tool, tmp_path):
    project = tmp_path / "kelo"
    project.mkdir()
    assert tool.apply(project, str(_dri(tmp_path, []))) == "kept"
    assert not (project / tool.OVERRIDE_NAME).exists()
    (project / tool.OVERRIDE_NAME).write_text(tool.MARKER + "services: {}\n")
    assert tool.apply(project, str(_dri(tmp_path, []))) == "removed"
    assert not (project / tool.OVERRIDE_NAME).exists()


def test_a_file_that_is_not_ours_is_never_touched(tool, tmp_path):
    project = tmp_path / "kelo"
    project.mkdir()
    own = project / tool.OVERRIDE_NAME
    own.write_text("services:\n  server:\n    environment: {X: '1'}\n")
    assert tool.apply(project, str(_dri(tmp_path, ["renderD128"])), gid_of=lambda path: 44) == "skipped"
    assert own.read_text().startswith("services:\n  server:")
    assert tool.apply(project, str(_dri(tmp_path, []))) == "kept"
    assert own.exists()


def test_the_entrypoint_uses_swiftshader_only_without_a_render_node():
    assert "/dev/dri/renderD*" in ENTRYPOINT                         # the container looks for a passed-in render node
    assert ENTRYPOINT.count("--use-angle=swiftshader") == 1          # the one place the software GL is named
    assert "$SWIFT_FLAGS" in ENTRYPOINT.split("exec browser")[1]    # and the browser line takes it from there
