from pathlib import Path

import pytest

from backend.app.sandbox import DockerSandbox, SandboxError, safe_relative_path


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "a/../../secret", "a\\b", "", "a//b", "a/./b", "a\x00b"])
def test_untrusted_paths_cannot_escape_workspace(name):
    with pytest.raises(ValueError):
        safe_relative_path(name)


def test_workspace_absolute_paths_are_explicitly_supported():
    assert safe_relative_path("/workspace/outputs/result.csv", allow_workspace_absolute=True) == Path("outputs/result.csv")
    with pytest.raises(ValueError):
        safe_relative_path("/workspace/../etc/passwd", allow_workspace_absolute=True)


def test_input_manifest_cannot_overwrite_outputs(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    with pytest.raises(ValueError, match="reserved"):
        sandbox.prepare_inputs([{"name": "outputs/result.csv", "content": "hidden expected result"}])


def test_runs_require_fresh_workspaces(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    sandbox.prepare_inputs([{"name": "input.csv", "content": "a\n1\n"}])
    with pytest.raises(SandboxError, match="fresh"):
        sandbox.prepare_inputs([])


def test_artifacts_reject_symlinks_after_container_stops(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    sandbox.prepare_inputs([])
    (sandbox.work_dir / "outputs" / "secret").symlink_to("/etc/passwd")
    with pytest.raises(SandboxError, match="regular"):
        sandbox.collect_artifacts()


def test_artifacts_reject_symlink_directories(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    sandbox.prepare_inputs([])
    (sandbox.work_dir / "outputs" / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxError, match="Symbolic"):
        sandbox.collect_artifacts()


def test_artifacts_are_only_regular_outputs_and_not_scripts_or_inputs(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    sandbox.prepare_inputs([{"name": "input.csv", "content": "a\n1\n"}])
    (sandbox.work_dir / "transform.py").write_text("pass")
    (sandbox.work_dir / "outputs" / "result.csv").write_text("a\n2\n")
    assert [item["name"] for item in sandbox.collect_artifacts()] == ["result.csv"]


def test_artifacts_cannot_be_collected_while_agent_is_running(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / "workspace")
    sandbox.prepare_inputs([])
    sandbox.started = True
    with pytest.raises(SandboxError, match="Stop"):
        sandbox.collect_artifacts()


def test_only_explicit_requested_scripts_are_published(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / 'workspace')
    sandbox.prepare_inputs([{'name': 'input.py', 'content': 'ORIGINAL'}])
    (sandbox.work_dir / 'deliverable.py').write_text('def main(): return 1')
    (sandbox.work_dir / 'temporary.py').write_text('TEMP')
    published = sandbox.publish_requested_scripts(['deliverable.py', 'input.py'], ['input.py'])
    assert published == ['deliverable.py']
    assert [a['name'] for a in sandbox.collect_artifacts()] == ['deliverable.py']
    assert (sandbox.work_dir / 'outputs' / 'deliverable.py').read_text() == 'def main(): return 1'


def test_publication_never_overwrites_canonical_deliverable(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / 'workspace')
    sandbox.prepare_inputs([])
    (sandbox.work_dir / 'script.py').write_text('root copy')
    (sandbox.work_dir / 'outputs' / 'script.py').write_text('canonical copy')
    assert sandbox.publish_requested_scripts(['script.py'], []) == []
    assert (sandbox.work_dir / 'outputs' / 'script.py').read_text() == 'canonical copy'


def test_publication_rejects_symlink_sources_and_parents(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / 'workspace')
    sandbox.prepare_inputs([])
    (tmp_path / 'outside.py').write_text('SECRET')
    (sandbox.work_dir / 'script.py').symlink_to(tmp_path / 'outside.py')
    with pytest.raises(SandboxError, match='Symbolic'):
        sandbox.publish_requested_scripts(['script.py'], [])
    (sandbox.work_dir / 'nested').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxError, match='Symbolic'):
        sandbox.publish_requested_scripts(['nested/outside.py'], [])


def test_reuse_preserves_prior_files_and_replaces_uploaded_leaf_symlink(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / 'workspace')
    sandbox.prepare_inputs([{'name': 'input.txt', 'content': 'first'}])
    (sandbox.work_dir / 'outputs' / 'script.py').write_text('prior code')
    outside = tmp_path / 'outside.txt'; outside.write_text('unchanged')
    (sandbox.work_dir / 'next.txt').symlink_to(outside)
    sandbox.prepare_inputs([{'name': 'next.txt', 'content': 'replacement'}], reuse=True)
    assert outside.read_text() == 'unchanged'
    assert not (sandbox.work_dir / 'next.txt').is_symlink()
    assert (sandbox.work_dir / 'outputs' / 'script.py').read_text() == 'prior code'
    assert (sandbox.work_dir / 'input.txt').read_text() == 'first'


def test_reused_workspace_rejects_symlink_upload_parent_and_outputs(tmp_path):
    sandbox = DockerSandbox(tmp_path.resolve() / 'workspace')
    sandbox.prepare_inputs([])
    (sandbox.work_dir / 'linked').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        sandbox.prepare_inputs([{'name': 'linked/escape.txt','content':'bad'}], reuse=True)
    assert not (tmp_path / 'escape.txt').exists()
    (sandbox.work_dir / 'outputs').rmdir()
    (sandbox.work_dir / 'outputs').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxError, match='link'):
        sandbox.prepare_inputs([], reuse=True)
