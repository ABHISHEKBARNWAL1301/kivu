"""File tools: read_file, list_directory, glob, grep, write_file, edit_file.

Writes show a diff and ask for approval first.

Still to build:
- move the path check into sandbox/policy.py, plus a denylist (.git/, keys.json, .env)
"""

from __future__ import annotations

import difflib
import os
import re
from collections.abc import Iterator
from pathlib import Path

from ..sandbox.approval import Approver
from .registry import Tool

SKIP_DIRS = {".git", ".venv", "venv", "kivu-venv", "node_modules", "__pycache__", ".idea", ".mypy_cache"}
MAX_RESULTS = 200


def file_tools(root: Path, approver: Approver) -> list[Tool]:
    root = root.resolve()

    def inside(path: str) -> Path:
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"{path} is outside the workspace")
        return target

    def rel(path: Path) -> str:
        return str(path.relative_to(root))

    def walk(base: Path) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                yield Path(dirpath) / name

    def read_file(path: str, offset: int = 1, limit: int = 500) -> str:
        lines = inside(path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(int(offset), 1)  # models sometimes send numbers as floats
        chunk = lines[start - 1 : start - 1 + int(limit)]
        body = "\n".join(f"{n:>5}\t{line}" for n, line in enumerate(chunk, start))
        end = start + len(chunk) - 1
        if end < len(lines):
            body += f"\n[lines {start}-{end} of {len(lines)}; pass offset={end + 1} to read more]"
        return body or "(empty file)"

    def list_directory(path: str = ".") -> str:
        target = inside(path)
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        return "\n".join(p.name + ("/" if p.is_dir() else "") for p in entries) or "(empty)"

    def glob(pattern: str, path: str = ".") -> str:
        base = inside(path)
        hits = [p for p in walk(base) if p.relative_to(base).match(pattern)]
        out = [rel(p) for p in hits[:MAX_RESULTS]]
        if len(hits) > MAX_RESULTS:
            out.append(f"[{len(hits) - MAX_RESULTS} more; narrow the pattern]")
        return "\n".join(out) or "no files match"

    def grep(pattern: str, path: str = ".", glob: str = "*") -> str:
        regex = re.compile(pattern)
        base = inside(path)
        files = [base] if base.is_file() else [p for p in walk(base) if p.match(glob)]
        out: list[str] = []
        for file in files:
            try:
                data = file.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:1024] or len(data) > 1_000_000:
                continue  # skip binary and huge files
            for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    out.append(f"{rel(file)}:{n}: {line.strip()[:200]}")
                    if len(out) >= MAX_RESULTS:
                        return "\n".join(out) + "\n[more matches; narrow the pattern or path]"
        return "\n".join(out) or "no matches"

    def diff(path: str, old: str, new: str) -> str:
        lines = difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="")
        return "\n".join(lines)

    def write_file(path: str, content: str) -> str:
        target = inside(path)
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        verb = "overwrite" if target.exists() else "create"
        approver.check("write", f"{verb} {path}", diff(path, old, content))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content.splitlines())} lines to {path}"

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        target = inside(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text not found; read the file again and copy the text exactly")
        if count > 1:
            raise ValueError(f"old_text matches {count} places; include more surrounding lines so it's unique")
        new = text.replace(old_text, new_text, 1)
        approver.check("write", f"edit {path}", diff(path, text, new))
        target.write_text(new, encoding="utf-8")
        return f"edited {path}"

    path_arg = {"type": "string", "description": "relative to the workspace root"}
    
    return [
        Tool(
            "read_file",
            "Read a text file with line numbers. Long files come in pages: pass offset to continue.",
            {
                "type": "object",
                "properties": {
                    "path": path_arg,
                    "offset": {"type": "integer", "description": "first line, 1-based (default 1)"},
                    "limit": {"type": "integer", "description": "number of lines (default 500)"},
                },
                "required": ["path"],
            },
            read_file,
        ),
        Tool(
            "list_directory",
            "List one directory. Folders end with '/'. Use glob to find files across folders.",
            {"type": "object", "properties": {"path": {**path_arg, "description": "default: ."}}},
            list_directory,
        ),
        Tool(
            "glob",
            "Find files by name pattern, e.g. '*.py', 'tests/test_*.py'. Skips .git and virtualenvs.",
            {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {**path_arg, "description": "folder to search, default: ."}},
                "required": ["pattern"],
            },
            glob,
        ),
        Tool(
            "grep",
            "Search file contents with a Python regex. Returns path:line: text. Use this to find code, not run_shell.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regular expression"},
                    "path": {**path_arg, "description": "file or folder, default: ."},
                    "glob": {"type": "string", "description": "only files matching this name pattern, e.g. '*.py'"},
                },
                "required": ["pattern"],
            },
            grep,
        ),
        Tool(
            "write_file",
            "Create a file, or replace a whole file. To change part of an existing file, use edit_file.",
            {
                "type": "object",
                "properties": {"path": path_arg, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            write_file,
        ),
        Tool(
            "edit_file",
            "Replace old_text with new_text in a file. old_text must match exactly once, "
            "including indentation; read the file first.",
            {
                "type": "object",
                "properties": {"path": path_arg, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"],
            },
            edit_file,
        ),
    ]
