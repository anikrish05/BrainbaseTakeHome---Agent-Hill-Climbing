"""A fresh, network-disabled Docker workspace for each agent run.

Generated commands never execute on the API host. Only the run workspace is
mounted, and output files are inspected after the container has stopped.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import time
import uuid
from typing import Any


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_ARTIFACTS = 32
MAX_TOOL_TEXT = 16_000


class SandboxError(RuntimeError):
    pass


class SandboxUnavailable(SandboxError):
    pass


def safe_relative_path(name: str, *, allow_workspace_absolute: bool = False) -> Path:
    """Validate names before touching the host filesystem or invoking Docker."""
    if not isinstance(name, str) or not name or len(name) > 240 or "\x00" in name:
        raise ValueError("File names must be nonempty strings of at most 240 characters.")
    if "\\" in name:
        raise ValueError("Use forward slashes in file paths.")
    if allow_workspace_absolute and name.startswith("/workspace/"):
        name = name[len("/workspace/"):]
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"..", "."} for part in name.split("/")):
        raise ValueError("Paths must stay inside the run workspace; '..' is not allowed.")
    if not path.parts or any(not part for part in name.split("/")):
        raise ValueError("Invalid file path.")
    return Path(*path.parts)


_health_cache: tuple[float, str, dict[str, Any]] | None = None


def sandbox_status(*, refresh: bool = False) -> dict[str, Any]:
    global _health_cache
    image = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
    now = time.monotonic()
    if not refresh and _health_cache and _health_cache[1] == image and now - _health_cache[0] < 10:
        return dict(_health_cache[2])
    docker = shutil.which("docker")
    result: dict[str, Any] = {"kind": "docker", "image": image, "ready": False}
    try:
        if not docker:
            raise SandboxUnavailable("Docker is not installed. Install/start Docker Desktop to execute agents.")
        probe = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if probe.returncode:
            raise SandboxUnavailable("Docker engine is unavailable. Start Docker Desktop and retry.")
        inspect = subprocess.run(
            [docker, "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if inspect.returncode:
            raise SandboxUnavailable(f"Sandbox image is missing. Run: docker pull {image}")
        result.update(ready=True, reason=None, image_id=inspect.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, SandboxUnavailable) as exc:
        result["reason"] = str(exc) or "Docker readiness check timed out."
    _health_cache = (now, image, result)
    return dict(result)


# This program is supplied via `python -I -c`; it is never loaded from a
# model-writable file. Isolated Python also excludes workspace import shadowing.
_FILE_PROGRAM = r'''
import json, os, pathlib, stat, sys
root = pathlib.Path('/workspace')
request = json.loads(sys.stdin.read())
operation = request['operation']
raw = request.get('path', '.')
if raw.startswith('/workspace/'):
    raw = raw[len('/workspace/'):]
if raw == '/workspace':
    raw = '.'
parts = pathlib.PurePosixPath(raw)
if parts.is_absolute() or '..' in parts.parts:
    raise ValueError('Path must stay inside /workspace')
path = root.joinpath(parts)
cursor = root
for part in parts.parts:
    cursor = cursor / part
    if cursor.is_symlink():
        raise ValueError('Symbolic links are not allowed for file tools')
if not path.resolve().is_relative_to(root):
    raise ValueError('Path must stay inside /workspace')
if operation == 'read':
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Path is not a regular file')
    limit = 128000
    with path.open('rb') as f:
        content = f.read(limit + 1)
    result = {'path': str(path.relative_to(root)), 'content': content[:limit].decode('utf-8', 'replace'), 'truncated': len(content) > limit}
elif operation == 'write':
    content = request['content'].encode('utf-8')
    if len(content) > 256000:
        raise ValueError('write_file is limited to 256 KB; generate larger files with Python')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_file():
        raise ValueError('Path is not a regular file')
    path.write_bytes(content)
    result = {'path': str(path.relative_to(root)), 'size': len(content)}
elif operation == 'list':
    if not path.is_dir():
        raise ValueError('Path is not a directory')
    entries = []
    for child in sorted(path.iterdir(), key=lambda x: x.name)[:200]:
        info = child.lstat()
        entries.append({'name': str(child.relative_to(root)), 'kind': 'symlink' if child.is_symlink() else 'directory' if child.is_dir() else 'file', 'size': info.st_size})
    result = {'entries': entries, 'truncated': len(list(path.iterdir())) > 200}
else:
    raise ValueError('Unknown operation')
print(json.dumps(result))
'''


_COMMAND_PROGRAM = r'''
import json, os, signal, subprocess, sys, tempfile, time
request = json.loads(sys.stdin.read())
timeout = max(1, min(30, int(request.get('timeout_s', 20))))
with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
    process = subprocess.Popen(['/bin/sh', '-lc', request['command']], cwd='/workspace', stdout=out, stderr=err, start_new_session=True)
    started = time.monotonic()
    reason = None
    while process.poll() is None:
        if time.monotonic() - started > timeout:
            reason = 'Command timed out'
            break
        if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > 512000:
            reason = 'Command output exceeded 512 KB'
            break
        time.sleep(0.04)
    # Kill background descendants too. A tool must not leave processes changing
    # files during later calls. Every invocation owns a new process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    out.seek(0); err.seek(0)
    stdout = out.read(16001); stderr = err.read(16001)
    print(json.dumps({'stdout': stdout[:16000].decode('utf-8', 'replace'), 'stderr': stderr[:16000].decode('utf-8', 'replace'), 'exit_code': process.returncode, 'error': reason, 'truncated': len(stdout) > 16000 or len(stderr) > 16000, 'duration_ms': round((time.monotonic() - started) * 1000)}))
'''


class DockerSandbox:
    def __init__(self, work_dir: Path, *, timeout_s: int = 120, image: str | None = None):
        self.work_dir = Path(work_dir).absolute()
        if "," in str(self.work_dir):
            raise SandboxError("Workspace paths may not contain commas (Docker --mount syntax).")
        if self.work_dir.is_symlink() or self.work_dir.resolve() != self.work_dir:
            raise SandboxError("Run workspace may not contain symbolic links in its path.")
        self.image = image or os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
        self.name = "agent-platform-" + uuid.uuid4().hex[:16]
        self.timeout_s = max(10, min(600, timeout_s))
        self.docker = shutil.which("docker")
        self.started = False
        self.uid = os.getuid() or 65534
        self.gid = os.getgid() or 65534

    def prepare_inputs(self, files: list[dict[str, Any]], *, reuse: bool = False) -> list[str]:
        """Write input files. `reuse` keeps a chat session's existing workspace across turns."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if not reuse and any(self.work_dir.iterdir()):
            raise SandboxError("Each run requires a fresh, empty workspace.")
        if len(files) > 20:
            raise ValueError("A run accepts at most 20 input files.")
        names: list[str] = []
        total = 0
        for item in files:
            relative = safe_relative_path(item["name"])
            if relative.parts[0] == "outputs":
                raise ValueError("The outputs directory is reserved for generated artifacts.")
            if relative.as_posix() in names:
                raise ValueError("Input file names must be unique.")
            content = str(item.get("content", "")).encode("utf-8")
            total += len(content)
            if len(content) > MAX_INPUT_BYTES or total > 5 * MAX_INPUT_BYTES:
                raise ValueError("Input files exceed the run upload limit.")
            destination = self.work_dir / relative
            if reuse:
                # A reused workspace may hold links the agent created in an earlier turn.
                # Never follow them from the host: refuse linked parents, replace a linked file.
                cursor = self.work_dir
                for part in relative.parts[:-1]:
                    cursor = cursor / part
                    if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
                        raise ValueError("Input path conflicts with a link or file in the session workspace.")
                if destination.is_symlink():
                    destination.unlink()
                elif destination.exists() and not destination.is_file():
                    raise ValueError("Input path conflicts with a directory in the session workspace.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            names.append(relative.as_posix())
        outputs = self.work_dir / "outputs"
        if outputs.is_symlink():
            raise SandboxError("The outputs directory may not be a link.")
        outputs.mkdir(exist_ok=reuse)
        if os.getuid() == 0:
            # Root-hosted API processes must still run generated code as nonroot.
            for directory, _, file_names in os.walk(self.work_dir):
                os.chown(directory, self.uid, self.gid)
                for file_name in file_names:
                    os.chown(Path(directory) / file_name, self.uid, self.gid)
        return names

    async def _docker(self, args: list[str], *, timeout: float = 8, input_bytes: bytes | None = None) -> tuple[int, str, str]:
        if not self.docker:
            raise SandboxUnavailable("Docker is not installed.")
        process = await asyncio.create_subprocess_exec(
            self.docker, *args,
            stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(input_bytes), timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        return process.returncode or 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    async def start(self) -> None:
        code, stdout, stderr = await self._docker([
            "run", "--detach", "--rm", "--pull", "never", "--name", self.name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
            "--ulimit", "fsize=5242880:5242880", "--ulimit", "nofile=128:128",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--user", f"{self.uid}:{self.gid}", "--workdir", "/workspace",
            "--mount", f"type=bind,src={self.work_dir},dst=/workspace",
            "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint", "python", self.image,
            "-I", "-c", f"import time; time.sleep({self.timeout_s + 30})",
        ], timeout=45)  # Container start is slow when many sandboxes launch at once.
        if code:
            raise SandboxUnavailable(f"Sandbox could not start: {stderr.strip()[:1000] or stdout.strip()[:1000]}")
        self.started = True

    async def stop(self) -> None:
        # Also remove a container when startup was cancelled after Docker created it.
        try:
            await self._docker(["rm", "--force", self.name], timeout=8)
        finally:
            self.started = False

    async def _run_program(self, program: str, payload: dict[str, Any], *, timeout: float = 8) -> dict[str, Any]:
        if not self.started:
            raise SandboxError("Sandbox is not running.")
        code, stdout, stderr = await self._docker(
            ["exec", "-i", self.name, "python", "-I", "-c", program], timeout=timeout,
            input_bytes=json.dumps(payload).encode(),
        )
        if code:
            return {"error": stderr.strip()[-MAX_TOOL_TEXT:] or "Sandbox command failed.", "exit_code": code}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SandboxError("Sandbox returned an invalid tool result.") from exc

    async def bash(self, command: str, timeout_s: int = 20) -> dict[str, Any]:
        if len(command) > 64_000:
            raise ValueError("Command is too long.")
        timeout_s = max(1, min(30, timeout_s))
        try:
            return await self._run_program(_COMMAND_PROGRAM, {"command": command, "timeout_s": timeout_s}, timeout=timeout_s + 5)
        except TimeoutError:
            await self.stop()
            raise SandboxError("Sandbox command stopped responding; the workspace was terminated.")

    async def read_file(self, path: str) -> dict[str, Any]:
        safe_relative_path(path, allow_workspace_absolute=True)
        return await self._run_program(_FILE_PROGRAM, {"operation": "read", "path": path})

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        safe_relative_path(path, allow_workspace_absolute=True)
        return await self._run_program(_FILE_PROGRAM, {"operation": "write", "path": path, "content": content})

    async def list_files(self, path: str = ".") -> dict[str, Any]:
        if path not in {".", "/workspace"}:
            safe_relative_path(path, allow_workspace_absolute=True)
        return await self._run_program(_FILE_PROGRAM, {"operation": "list", "path": path})

    def publish_requested_scripts(self, names: list[str], input_names: list[str]) -> list[str]:
        """Recover explicitly requested Python deliverables misplaced in the root.

        Never infer deliverables from arbitrary workspace contents, reuse an input,
        overwrite a canonical output, or follow symbolic links. Container is stopped.
        """
        if self.started:
            raise SandboxError("Stop the sandbox before publishing artifacts.")
        root = self.work_dir / "outputs"
        if root.is_symlink() or not root.is_dir():
            raise SandboxError("The outputs directory must be a regular directory.")
        published = []
        for name in dict.fromkeys(names):
            relative = safe_relative_path(name)
            if relative.suffix != ".py" or name in input_names:
                continue
            source, destination = self.work_dir / relative, root / relative
            if destination.exists() or destination.is_symlink() or not source.exists():
                continue
            for path in (source, destination):
                if any(p.is_symlink() for p in (path, *path.parents) if p != self.work_dir and self.work_dir in p.parents):
                    raise SandboxError("Symbolic links are not permitted in published artifacts.")
            if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size > MAX_ARTIFACT_BYTES:
                raise SandboxError("Only size-limited regular scripts can be published.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            published.append(relative.as_posix())
        return published

    def collect_artifacts(self) -> list[dict[str, Any]]:
        if self.started:
            raise SandboxError("Stop the sandbox before collecting artifacts.")
        root = self.work_dir / "outputs"
        if root.is_symlink() or not root.is_dir():
            raise SandboxError("The outputs directory must be a regular directory.")
        artifacts: list[dict[str, Any]] = []
        total = 0
        for directory, subdirs, files in os.walk(root, followlinks=False):
            for name in subdirs:
                if (Path(directory) / name).is_symlink():
                    raise SandboxError("Symbolic links are not permitted in output artifacts.")
            for name in sorted(files):
                path = Path(directory) / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                    raise SandboxError("Only regular files can be collected as artifacts.")
                total += info.st_size
                if info.st_size > MAX_ARTIFACT_BYTES or total > MAX_TOTAL_ARTIFACT_BYTES or len(artifacts) >= MAX_ARTIFACTS:
                    raise SandboxError("Generated artifacts exceed the file count or size limit.")
                artifacts.append({"name": path.relative_to(root).as_posix(), "path": str(path), "size": info.st_size})
        return artifacts
