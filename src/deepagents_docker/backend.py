from __future__ import annotations

import atexit
import tempfile
import uuid
import weakref
from collections.abc import Callable
from functools import partial
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from ._docker import (
    docker_available,
    format_docker_error,
    run_docker,
)
from .errors import DockerError

DEFAULT_EXECUTE_TIMEOUT = 120
DEFAULT_IMAGE = "python:3.12-bookworm"
CONTAINER_WORKDIR = "/shared"
DOCKER_INFO_TIMEOUT = 30
DOCKER_START_TIMEOUT = 600
CLEANUP_TIMEOUT = 15

# `docker exec` makes each exec its own session/process-group leader, so the wrapper records
# its PID (== the process group id) and a later `kill -9 -<pgid>` reaps the whole tree,
# including children reparented to PID 1. $1 is the pid file, $2 the user command.
_EXEC_WRAPPER = """
echo $$ > "$1" 2>/dev/null
sh -c "$2"
status=$?
rm -f "$1" 2>/dev/null
exit $status
"""

# Kill the process group recorded by `_EXEC_WRAPPER`. $1 is the pid file.
_KILL_WRAPPER = """
pgid=""
[ -f "$1" ] && read pgid < "$1"
rm -f "$1" 2>/dev/null
[ -n "$pgid" ] && kill -9 -"$pgid" 2>/dev/null
exit 0
"""


def _atexit_close(ref: weakref.ReferenceType[DockerSandbox]) -> None:
    """Close a sandbox at interpreter exit without keeping it alive until then."""
    sandbox = ref()
    if sandbox is not None:
        sandbox.close()


class DockerSandbox(FilesystemBackend, SandboxBackendProtocol):
    """Docker-backed sandbox backend for DeepAgents."""

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        allow_outbound_traffic: bool = True,
        shared_dir: str | Path | None = None,
        timeout: int = DEFAULT_EXECUTE_TIMEOUT,
        max_output_bytes: int = 100_000,
        memory: str = "256m",
        cpus: float = 0.5,
        pids_limit: int = 128,
        auto_remove: bool = True,
        extra_run_args: list[str] | None = None,
    ) -> None:
        """Create a sandbox container and shared directory.

        Args:
            image: Docker image for command execution (default: official ``python:3.12-bookworm``).
            allow_outbound_traffic: Allow/deny outbound network traffic (default: allow).
            shared_dir: Host directory shared with the container. A temporary directory is
                created when omitted.
            timeout: Default command timeout in seconds.
            max_output_bytes: Maximum stdout/stderr captured per command. Output is streamed
                and the command is killed once a stream reaches this cap, so a command writing
                unbounded output cannot exhaust host memory.
            memory: Docker memory limit (for example ``"256m"``).
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
        if max_output_bytes <= 0:
            msg = f"max_output_bytes must be positive, got {max_output_bytes}"
            raise ValueError(msg)

        self._owns_shared_dir = shared_dir is None
        self._shared_dir = Path(
            tempfile.mkdtemp(prefix="deepagents-docker-shared-")
            if shared_dir is None
            else shared_dir,
        ).resolve()
        self._shared_dir.mkdir(parents=True, exist_ok=True)

        super().__init__(root_dir=self._shared_dir, virtual_mode=True)

        self._image = image
        self._default_timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._network_mode = "bridge" if allow_outbound_traffic else "none"
        self._auto_remove = auto_remove
        self._extra_run_args = list(extra_run_args or [])

        self._container_id: str = f"{uuid.uuid4().hex[:12]}"
        self._container_name = f"deepagents-docker-{self._container_id}"
        self._closed = False
        self._atexit_hook: Callable[[], None] | None = None

        self._start_container()
        self._atexit_hook = partial(_atexit_close, weakref.ref(self))
        atexit.register(self._atexit_hook)

    @property
    def shared_dir(self) -> Path:
        """Host path of the folder shared with the container."""
        return self._shared_dir

    @property
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        return self._container_id

    def _start_container(self) -> None:
        if not docker_available(timeout=DOCKER_INFO_TIMEOUT):
            msg = (
                "Docker is not available. Install Docker, ensure the daemon is running, "
                f"and pull the default image with `docker pull {DEFAULT_IMAGE}`"
            )
            raise DockerError(msg)

        run_args = [
            "run",
            "-d",
            # Docker's init process reaps orphaned children, so killed commands do not
            # pile up as zombies against `--pids-limit`.
            "--init",
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
            "-v",
            f"{self._shared_dir}:{CONTAINER_WORKDIR}:rw",
            "-w",
            CONTAINER_WORKDIR,
            *self._extra_run_args,
            self._image,
            "sleep",
            "infinity",
        ]
        try:
            result = run_docker(run_args, timeout=DOCKER_START_TIMEOUT)
        except DockerError:
            # A timed-out `docker run` may still have created the container.
            self._force_remove_container()
            raise
        if result.returncode != 0:
            self._force_remove_container()
            msg = format_docker_error(result)
            raise DockerError(f"failed to start sandbox container: {msg}")

    def _force_remove_container(self) -> None:
        """Best-effort `docker rm -f`, used on failed startup and on close."""
        try:
            run_docker(["rm", "-f", self._container_name], timeout=CLEANUP_TIMEOUT)
        except DockerError:
            pass

    def _kill_exec_group(self, pid_file: str) -> None:
        """Kill the in-container process group left behind by a timed-out/killed exec."""
        try:
            run_docker(
                ["exec", self._container_name, "sh", "-c", _KILL_WRAPPER, "sh", pid_file],
                timeout=CLEANUP_TIMEOUT,
                max_output_bytes=4096,
            )
        except DockerError:
            pass

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

        pid_file = f"/tmp/.deepagents-exec-{uuid.uuid4().hex}"  # noqa: S108
        docker_args = [
            "exec",
            self._container_name,
            "sh",
            "-c",
            _EXEC_WRAPPER,
            "sh",
            pid_file,
            command,
        ]

        try:
            completed = run_docker(
                docker_args,
                timeout=effective_timeout,
                max_output_bytes=self._max_output_bytes,
            )
        except DockerError as exc:
            detail = str(exc)
            if "timed out" in detail:
                # Killing the `docker exec` client leaves the command running in the
                # container; reap its process group so it cannot burn the container's
                # CPU/PID budget forever.
                self._kill_exec_group(pid_file)
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

        if completed.truncated:
            self._kill_exec_group(pid_file)

        output_parts: list[str] = []
        if completed.stdout:
            output_parts.append(completed.stdout)
        if completed.stderr:
            stderr_lines = completed.stderr.strip().split("\n")
            output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

        output = "\n".join(output_parts) if output_parts else "<no output>"
        truncated = completed.truncated
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            truncated = True
        if truncated:
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
        if completed.truncated:
            output += " The command was terminated after exceeding the output limit."

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

        if self._atexit_hook is not None:
            atexit.unregister(self._atexit_hook)

        # Best-effort shutdown; the container may already be gone.
        try:
            run_docker(["stop", "-t", "2", self._container_name], timeout=30)
        except DockerError:
            pass

        if self._auto_remove:
            self._force_remove_container()

        if self._owns_shared_dir:
            import shutil

            shutil.rmtree(self._shared_dir, ignore_errors=True)

    def __enter__(self) -> DockerSandbox:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001, S110
            pass
