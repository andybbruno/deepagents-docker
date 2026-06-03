"""DockerSandbox: isolated shell execution with host-backed workspace files."""

from __future__ import annotations

import atexit
import shlex
import tempfile
import uuid
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from ._docker import (
    docker_available,
    format_docker_error,
    inspect_container_id,
    run_docker,
)
from .errors import DockerError

DEFAULT_EXECUTE_TIMEOUT = 120
DEFAULT_IMAGE = "python:3.12-bookworm"
CONTAINER_WORKDIR = "/workspace"


class DockerSandbox(FilesystemBackend, SandboxBackendProtocol):
    """Filesystem backend with shell commands executed inside a Docker container.

    File operations (`ls`, `read`, `write`, `edit`, `grep`, `glob`) run against a
    dedicated workspace directory on the host via `FilesystemBackend` with
    `virtual_mode=True`. The same directory is bind-mounted into the container at
    `/workspace`, and the `execute` tool runs commands there with Docker resource
    and security limits.

    This is defense in depth, not a perfect isolation boundary. Do not mount
    secrets into the workspace, keep Docker patched, and prefer microVMs for
    hostile multi-tenant workloads.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        allow_outbound_traffic: bool = True,
        workspace_dir: str | Path | None = None,
        timeout: int = DEFAULT_EXECUTE_TIMEOUT,
        max_output_bytes: int = 100_000,
        memory: str = "512m",
        cpus: float = 1.0,
        pids_limit: int = 128,
        auto_remove: bool = True,
        extra_run_args: list[str] | None = None,
    ) -> None:
        """Create a sandbox container and workspace directory.

        Args:
            image: Docker image for command execution (default: official ``python:3.12-bookworm``).
            allow_outbound_traffic: Allow/deny outbound network traffic (default: allow).
            workspace_dir: Host directory for agent files. A temporary directory is
                created when omitted.
            timeout: Default command timeout in seconds.
            max_output_bytes: Maximum combined stdout/stderr captured per command.
            memory: Docker memory limit (for example ``"512m"``).
            cpus: Docker CPU limit.
            pids_limit: Maximum number of PIDs inside the container.
            auto_remove: Remove the container on ``close()``.
            extra_run_args: Additional ``docker run`` flags appended before the image.
        """
        if timeout <= 0:
            msg = f"timeout must be positive, got {timeout}"
            raise ValueError(msg)
        if cpus <= 0:
            msg = f"cpus must be positive, got {cpus}"
            raise ValueError(msg)
        if pids_limit <= 0:
            msg = f"pids_limit must be positive, got {pids_limit}"
            raise ValueError(msg)

        self._owns_workspace = workspace_dir is None
        self._workspace = Path(
            tempfile.mkdtemp(prefix="deepagents-docker-")
            if workspace_dir is None
            else workspace_dir,
        ).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)

        super().__init__(
            root_dir=self._workspace,
            virtual_mode=True,
            max_file_size_mb=10,
        )

        self._image = image
        self._default_timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._network_mode = "bridge" if allow_outbound_traffic else "none"
        self._auto_remove = auto_remove
        self._extra_run_args = list(extra_run_args or [])

        self._container_name = f"deepagents-docker-{uuid.uuid4().hex[:12]}"
        self._container_id: str | None = None
        self._closed = False

        self._start_container()
        atexit.register(self.close)

    @property
    def workspace_dir(self) -> Path:
        """Host path backing the agent workspace."""
        return self._workspace

    @property
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        return self._container_id or self._container_name

    def _start_container(self) -> None:
        if not docker_available():
            msg = (
                "Docker is not available. Install Docker, ensure the daemon is running, "
                f"and pull the default image with `docker pull {DEFAULT_IMAGE}`"
            )
            raise DockerError(msg)

        run_args = [
            "run",
            "-d",
            "--name",
            self._container_name,
            "--network",
            self._network_mode,
            "--cpus",
            str(self._cpus),
            "--memory",
            self._memory,
            "--pids-limit",
            str(self._pids_limit),
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs",
            "/var/tmp:rw,noexec,nosuid,size=64m",
            "-v",
            f"{self._workspace}:{CONTAINER_WORKDIR}:rw",
            "-w",
            CONTAINER_WORKDIR,
            *self._extra_run_args,
            self._image,
            "sleep",
            "infinity",
        ]
        result = run_docker(run_args)
        if result.returncode != 0:
            msg = format_docker_error(result)
            raise DockerError(f"failed to start sandbox container: {msg}")

        self._container_id = inspect_container_id(self._container_name)

    def _wrap_command(self, command: str) -> str:
        """Run agent commands from the container workspace directory."""
        return f"cd {shlex.quote(CONTAINER_WORKDIR)} && {command}"

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the sandbox container."""
        if self._closed:
            return ExecuteResponse(
                output="Error: Sandbox has been closed.",
                exit_code=1,
                truncated=False,
            )

        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        wrapped = self._wrap_command(command)
        docker_args = [
            "exec",
            self._container_name,
            "sh",
            "-c",
            wrapped,
        ]

        try:
            completed = run_docker(docker_args, timeout=effective_timeout)
        except DockerError as exc:
            detail = str(exc)
            if "timed out" in detail:
                if timeout is not None:
                    msg = (
                        f"Error: Command timed out after {effective_timeout} seconds "
                        "(custom timeout). The command may be stuck or require more time."
                    )
                else:
                    msg = (
                        f"Error: Command timed out after {effective_timeout} seconds. "
                        "For long-running commands, re-run using the timeout parameter."
                    )
                return ExecuteResponse(output=msg, exit_code=124, truncated=False)
            if "not found on PATH" in detail:
                return ExecuteResponse(
                    output=(
                        "Error executing command (FileNotFoundError): "
                        "docker executable not found on PATH"
                    ),
                    exit_code=1,
                    truncated=False,
                )
            return ExecuteResponse(
                output=f"Error executing command (DockerError): {exc}",
                exit_code=1,
                truncated=False,
            )

        output_parts: list[str] = []
        if completed.stdout:
            output_parts.append(completed.stdout)
        if completed.stderr:
            stderr_lines = completed.stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

        output = "\n".join(output_parts) if output_parts else "<no output>"
        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True

        if completed.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {completed.returncode}"

        return ExecuteResponse(
            output=output,
            exit_code=completed.returncode,
            truncated=truncated,
        )

    def close(self) -> None:
        """Stop and remove the sandbox container."""
        if self._closed:
            return
        self._closed = True

        stop = run_docker(["stop", "-t", "2", self._container_name], timeout=30)
        if stop.returncode != 0 and "No such container" not in stop.stderr:
            # Best-effort shutdown; container may already be gone.
            pass

        if self._auto_remove:
            run_docker(["rm", "-f", self._container_name], timeout=30)

        if self._owns_workspace:
            import shutil

            shutil.rmtree(self._workspace, ignore_errors=True)

    def __enter__(self) -> DockerSandbox:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110
            pass
