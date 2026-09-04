from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Protocol


MAX_OUTPUT_CHARS = 40_000
MAX_READ_LINES = 800
MAX_LIST_ENTRIES = 600


class ToolUI(Protocol):
    def tool_call(self, name: str, detail: str) -> None: ...

    def tool_result(self, detail: str, ok: bool = True) -> None: ...

    def approve(self, command: str) -> bool: ...


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS, *, tail: bool = False) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if tail:
        return f"[output clipped; showing last {limit} characters]\n{text[-limit:]}", True
    return f"{text[:limit]}\n[output clipped after {limit} characters]", True


class Toolbox:
    _RISKY = re.compile(
        r"(^|[;&|]\s*)(sudo|rm|rmdir|shutdown|reboot|halt|mkfs|diskutil|dd|killall)\b"
        r"|\bgit\s+reset\s+--hard\b",
        re.IGNORECASE,
    )

    def __init__(self, cwd: str | Path, ui: ToolUI, approve_all: bool = False) -> None:
        resolved = Path(cwd).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"working directory does not exist: {resolved}")
        self.cwd = resolved
        self.ui = ui
        self.approve_all = approve_all

    def functions(self) -> list[object]:
        return [
            self.read_file,
            self.write_file,
            self.edit_file,
            self.list_directory,
            self.change_directory,
            self.run_shell,
        ]

    def _path(self, path: str) -> Path:
        candidate = Path(os.path.expandvars(path)).expanduser()
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        return candidate.resolve()

    def read_file(self, path: str, offset: int = 1, limit: int = 400) -> dict[str, object]:
        """Read a UTF-8 text file by line range.

        Args:
            path: Absolute path or a path relative to the current working directory.
            offset: First line to read, starting at 1.
            limit: Maximum number of lines to return, up to 800.
        """
        target = self._path(path)
        self.ui.tool_call("read", str(target))
        try:
            if offset < 1:
                raise ValueError("offset must be at least 1")
            limit = max(1, min(limit, MAX_READ_LINES))
            raw = target.read_bytes()
            if b"\x00" in raw[:8192]:
                raise ValueError("file appears to be binary")
            lines = raw.decode("utf-8", errors="replace").splitlines()
            selected = lines[offset - 1 : offset - 1 + limit]
            content, clipped = _clip("\n".join(selected))
            truncated = clipped or offset - 1 + len(selected) < len(lines)
            self.ui.tool_result(f"{len(selected)} of {len(lines)} lines")
            return {
                "path": str(target),
                "content": content,
                "offset": offset,
                "total_lines": len(lines),
                "truncated": truncated,
            }
        except Exception as exc:
            self.ui.tool_result(str(exc), ok=False)
            return {"error": str(exc), "path": str(target)}

    def write_file(self, path: str, content: str) -> dict[str, object]:
        """Create or completely overwrite a UTF-8 text file, including parent directories.

        Args:
            path: Absolute path or a path relative to the current working directory.
            content: Complete new file content.
        """
        target = self._path(path)
        self.ui.tool_call("write", str(target))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
            self.ui.tool_result(f"wrote {size} bytes")
            return {"path": str(target), "bytes_written": size}
        except Exception as exc:
            self.ui.tool_result(str(exc), ok=False)
            return {"error": str(exc), "path": str(target)}

    def edit_file(self, path: str, old_text: str, new_text: str) -> dict[str, object]:
        """Replace one exact, unique text block in a UTF-8 file.

        Args:
            path: Absolute path or a path relative to the current working directory.
            old_text: Exact text that must occur once in the file.
            new_text: Replacement text.
        """
        target = self._path(path)
        self.ui.tool_call("edit", str(target))
        try:
            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found")
            if count > 1:
                raise ValueError(f"old_text is not unique; found {count} matches")
            target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            self.ui.tool_result("replaced 1 block")
            return {"path": str(target), "replacements": 1}
        except Exception as exc:
            self.ui.tool_result(str(exc), ok=False)
            return {"error": str(exc), "path": str(target)}

    def list_directory(self, path: str = ".", depth: int = 2) -> dict[str, object]:
        """List a directory as a small tree.

        Args:
            path: Absolute path or a path relative to the current working directory.
            depth: Number of directory levels to include, from 1 to 5.
        """
        target = self._path(path)
        self.ui.tool_call("list", str(target))
        try:
            if not target.is_dir():
                raise ValueError("path is not a directory")
            depth = max(1, min(depth, 5))
            entries: list[str] = []
            for root, dirs, files in os.walk(target):
                root_path = Path(root)
                level = len(root_path.relative_to(target).parts)
                dirs.sort()
                files.sort()
                if level >= depth:
                    dirs[:] = []
                prefix = "  " * level
                if level:
                    entries.append(f"{prefix}{root_path.name}/")
                entries.extend(f"{prefix}  {name}" for name in files)
                if len(entries) >= MAX_LIST_ENTRIES:
                    entries = entries[:MAX_LIST_ENTRIES]
                    entries.append("[listing clipped]")
                    break
            content = "\n".join(entries) or "(empty)"
            self.ui.tool_result(f"{len(entries)} entries")
            return {"path": str(target), "content": content}
        except Exception as exc:
            self.ui.tool_result(str(exc), ok=False)
            return {"error": str(exc), "path": str(target)}

    def change_directory(self, path: str) -> dict[str, object]:
        """Change the persistent working directory used by all later tools.

        Args:
            path: Existing directory, absolute or relative to the current working directory.
        """
        target = self._path(path)
        self.ui.tool_call("cd", str(target))
        if not target.is_dir():
            message = "directory does not exist"
            self.ui.tool_result(message, ok=False)
            return {"error": message, "path": str(target)}
        self.cwd = target
        self.ui.tool_result(str(target))
        return {"cwd": str(target)}

    def run_shell(self, command: str, timeout_seconds: int = 120) -> dict[str, object]:
        """Run a shell command on the computer. Use this for mv, cp, mkdir, search, git, tests, and other terminal programs.

        Args:
            command: Complete shell command to run.
            timeout_seconds: Stop the command after this many seconds, from 1 to 1800.
        """
        self.ui.tool_call("$", command)
        if self._RISKY.search(command) and not self.approve_all and not self.ui.approve(command):
            message = "cancelled by user"
            self.ui.tool_result(message, ok=False)
            return {"error": message, "command": command}

        timeout_seconds = max(1, min(timeout_seconds, 1800))
        shell = os.environ.get("SHELL") or "/bin/sh"
        try:
            completed = subprocess.run(
                command,
                shell=True,
                executable=shell,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            stdout, stdout_clipped = _clip(completed.stdout)
            stderr, stderr_clipped = _clip(completed.stderr, tail=True)
            ok = completed.returncode == 0
            self.ui.tool_result(f"exit {completed.returncode}", ok=ok)
            return {
                "command": command,
                "cwd": str(self.cwd),
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_clipped or stderr_clipped,
            }
        except subprocess.TimeoutExpired as exc:
            message = f"timed out after {timeout_seconds}s"
            self.ui.tool_result(message, ok=False)
            return {
                "error": message,
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            }
        except Exception as exc:
            self.ui.tool_result(str(exc), ok=False)
            return {"error": str(exc), "command": command}

