"""Safe workspace-relative path validation shared by checkpoint and durable undo."""

from pathlib import Path, PurePosixPath, PureWindowsPath


def safe_relative(value: str) -> bool:
    """Whether a manifest path is relative and contains no traversal components."""
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    return (
        "\\" not in value
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and bool(path.parts)
        and all(part not in ("..", "") for part in path.parts)
    )


def validate_target(workspace: Path, rel: str) -> Path:
    """Reject symlink traversal even when its current target remains inside the workspace."""
    if not safe_relative(rel):
        raise ValueError("unsafe relative path")
    target = workspace / rel
    target.resolve(strict=False).relative_to(workspace)
    if target.is_symlink():
        raise ValueError("symlink target")
    parent = target.parent
    while parent != workspace:
        if parent.is_symlink():
            raise ValueError("symlinked parent")
        parent = parent.parent
    return target
