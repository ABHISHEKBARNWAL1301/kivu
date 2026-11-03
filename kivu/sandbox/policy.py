"""Decide whether an action is allowed, denied or needs approval.

To build:
- workspace root: file tools can't escape it (resolve symlinks and ..)
- shell commands: allowlist, denylist, or "ask" for anything risky
- later: OS-level isolation (macOS sandbox-exec, a container, or a separate user)
"""
