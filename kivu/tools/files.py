"""File tools: read_file, list_directory.

Still to build:
- write_file, edit_file (after the sandbox exists)
- move the path check into sandbox/policy.py
"""

from __future__ import annotations

from pathlib import Path

from .registry import Tool


def file_tools(root: Path) -> list[Tool]:
    root = root.resolve()

    def inside(path: str) -> Path:
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"{path} is outside the workspace")
        return target

    def read_file(path: str) -> str:
        return inside(path).read_text(encoding="utf-8", errors="replace")

    def list_directory(path: str = ".") -> str:
        target = inside(path)
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        return "\n".join(p.name + ("/" if p.is_dir() else "") for p in entries) or "(empty)"

    return [
        Tool(
            "read_file",
            "Read a UTF-8 text file. Path is relative to the workspace root.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            read_file,
        ),
        Tool(
            "list_directory",
            "List files and folders in a directory. Folders end with '/'.",
            {"type": "object", "properties": {"path": {"type": "string", "description": "default: ."}}},
            list_directory,
        ),
    ]
