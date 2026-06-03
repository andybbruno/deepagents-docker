"""Unit tests for DockerSandbox (Docker CLI mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deepagents_docker import DockerError, DockerSandbox
from deepagents_docker._docker import DockerRunResult
from deepagents_docker.backend import DEFAULT_IMAGE


def _docker_run_ok() -> DockerRunResult:
    return DockerRunResult(returncode=0, stdout="", stderr="")


def _make_run_docker_side_effect(**exec_config: object):
    """Build a side_effect for mocked run_docker (handles run/exec/stop/rm)."""

    def _run_docker(args: list[str], **kwargs: object) -> DockerRunResult:
        if args[0] == "exec":
            if "error" in exec_config:
                raise exec_config["error"]
            return exec_config.get(
                "exec",
                DockerRunResult(returncode=0, stdout="", stderr=""),
            )
        return _docker_run_ok()

    return _run_docker


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="abc123container")
def test_default_image_is_python_bookworm(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        run_args = run_docker.call_args_list[0][0][0]
        assert DEFAULT_IMAGE in run_args
        assert run_args[run_args.index(DEFAULT_IMAGE) + 1 : run_args.index(DEFAULT_IMAGE) + 3] == [
            "sleep",
            "infinity",
        ]
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="abc123container")
def test_start_container_applies_security_flags(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        run_args = run_docker.call_args_list[0][0][0]
        assert "run" in run_args
        assert "--network" in run_args and "bridge" in run_args
        assert "--cap-drop" in run_args and "ALL" in run_args
        assert "--read-only" in run_args
        assert f"{tmp_path.resolve()}:/workspace:rw" in " ".join(run_args)
        assert sandbox.id == "abc123container"
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_start_container_disables_outbound_traffic(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, allow_outbound_traffic=False)
    try:
        run_args = run_docker.call_args_list[0][0][0]
        network_index = run_args.index("--network")
        assert run_args[network_index + 1] == "none"
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_start_container_applies_resource_limits_and_extra_args(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(
        workspace_dir=tmp_path,
        memory="1g",
        cpus=2.5,
        pids_limit=256,
        extra_run_args=["--env", "FOO=bar"],
    )
    try:
        run_args = run_docker.call_args_list[0][0][0]
        assert run_args[run_args.index("--memory") + 1] == "1g"
        assert run_args[run_args.index("--cpus") + 1] == "2.5"
        assert run_args[run_args.index("--pids-limit") + 1] == "256"
        assert run_args[run_args.index("--env") + 1] == "FOO=bar"
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.inspect_container_id")
@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
def test_raises_when_container_start_fails(
    _available: MagicMock,
    run_docker: MagicMock,
    inspect: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = DockerRunResult(returncode=1, stdout="", stderr="image not found")

    with pytest.raises(DockerError, match="failed to start sandbox container: image not found"):
        DockerSandbox(workspace_dir=tmp_path)

    inspect.assert_not_called()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", side_effect=DockerError("inspect failed"))
def test_raises_when_container_inspect_fails(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    with pytest.raises(DockerError, match="inspect failed"):
        DockerSandbox(workspace_dir=tmp_path)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"timeout": 0}, "timeout must be positive"),
        ({"cpus": 0}, "cpus must be positive"),
        ({"pids_limit": -1}, "pids_limit must be positive"),
    ],
)
@patch("deepagents_docker.backend.docker_available", return_value=True)
def test_constructor_rejects_invalid_limits(
    _available: MagicMock,
    kwargs: dict[str, int],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        DockerSandbox(**kwargs)


@patch("deepagents_docker.backend.docker_available", return_value=False)
def test_raises_when_docker_unavailable(_available: MagicMock) -> None:
    with pytest.raises(DockerError, match="Docker is not available"):
        DockerSandbox(image="missing:latest")


def test_docker_error_is_public_runtime_error() -> None:
    assert issubclass(DockerError, RuntimeError)


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_wraps_command_and_returns_output(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        exec=DockerRunResult(returncode=0, stdout="hello\n", stderr=""),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        result = sandbox.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.output

        exec_args = run_docker.call_args_list[1][0][0]
        assert exec_args[:4] == ["exec", "-w", "/workspace", sandbox._container_name]
        shell_cmd = exec_args[-1]
        assert shell_cmd.startswith("cd /workspace && ")
        assert "echo hello" in shell_cmd
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_formats_stderr_and_nonzero_exit(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        exec=DockerRunResult(returncode=2, stdout="", stderr="something broke\n"),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        result = sandbox.execute("false")
        assert result.exit_code == 2
        assert "[stderr] something broke" in result.output
        assert "Exit code: 2" in result.output
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_reports_no_output(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        exec=DockerRunResult(returncode=0, stdout="", stderr=""),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        result = sandbox.execute("true")
        assert result.output == "<no output>"
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_truncates_large_output(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        exec=DockerRunResult(returncode=0, stdout="x" * 200, stderr=""),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path, max_output_bytes=50)
    try:
        result = sandbox.execute("printf x")
        assert result.truncated is True
        assert len(result.output) <= 50 + len("\n\n... Output truncated at 50 bytes.")
        assert "Output truncated at 50 bytes" in result.output
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_rejects_empty_command(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        result = sandbox.execute("")
        assert result.exit_code == 1
        assert "non-empty string" in result.output
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_after_close_returns_error(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    sandbox.close()
    result = sandbox.execute("echo hello")
    assert result.exit_code == 1
    assert "closed" in result.output.lower()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_timeout_with_custom_message(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        error=DockerError("docker command timed out after 1 seconds"),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path, timeout=1)
    try:
        result = sandbox.execute("sleep 10", timeout=1)
        assert result.exit_code == 124
        assert "custom timeout" in result.output.lower()
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_timeout_with_default_message(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        error=DockerError("docker command timed out after 120 seconds"),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        result = sandbox.execute("sleep 10")
        assert result.exit_code == 124
        assert "timeout parameter" in result.output.lower()
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_when_docker_binary_missing(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.side_effect = _make_run_docker_side_effect(
        error=DockerError("docker executable not found on PATH"),
    )

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        result = sandbox.execute("echo hello")
        assert result.exit_code == 1
        assert "not found on PATH" in result.output
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_execute_rejects_non_positive_timeout(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    try:
        with pytest.raises(ValueError, match="timeout must be positive"):
            sandbox.execute("echo hello", timeout=0)
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_write_and_read_via_virtual_paths(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, image="test-image:local")
    try:
        write_result = sandbox.write("/notes.txt", "alpha\n")
        assert write_result.error is None
        assert (tmp_path / "notes.txt").read_text() == "alpha\n"

        read_result = sandbox.read("/notes.txt")
        assert read_result.error is None
        assert read_result.file_data is not None
        assert "alpha" in read_result.file_data["content"]
    finally:
        sandbox.close()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_close_stops_and_removes_container(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    container_name = sandbox._container_name
    sandbox.close()

    stop_call = run_docker.call_args_list[1]
    rm_call = run_docker.call_args_list[2]
    assert stop_call[0][0] == ["stop", "-t", "2", container_name]
    assert rm_call[0][0] == ["rm", "-f", container_name]


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_close_is_idempotent(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    sandbox.close()
    sandbox.close()

    assert len(run_docker.call_args_list) == 3


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_close_skips_remove_when_auto_remove_disabled(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path, auto_remove=False)
    sandbox.close()

    assert len(run_docker.call_args_list) == 2
    assert run_docker.call_args_list[1][0][0][0] == "stop"


@patch("deepagents_docker.backend.tempfile.mkdtemp")
@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_close_removes_owned_workspace(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    mkdtemp: MagicMock,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "owned-workspace"
    workspace.mkdir()
    mkdtemp.return_value = str(workspace)
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox()
    sandbox.close()

    assert not workspace.exists()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_close_preserves_user_workspace(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    sandbox = DockerSandbox(workspace_dir=tmp_path)
    sandbox.close()

    assert tmp_path.exists()


@patch("deepagents_docker.backend.run_docker")
@patch("deepagents_docker.backend.docker_available", return_value=True)
@patch("deepagents_docker.backend.inspect_container_id", return_value="cid")
def test_context_manager_closes_sandbox(
    _inspect: MagicMock,
    _available: MagicMock,
    run_docker: MagicMock,
    tmp_path: Path,
) -> None:
    run_docker.return_value = _docker_run_ok()

    with DockerSandbox(workspace_dir=tmp_path) as sandbox:
        assert sandbox.id == "cid"

    assert len(run_docker.call_args_list) == 3
