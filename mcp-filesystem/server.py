import os
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get("MCP_FILESYSTEM_ROOT", "/workspace-data")).resolve()
READ_ONLY = os.environ.get("MCP_FILESYSTEM_READ_ONLY", "false").lower() in {"1", "true", "yes"}
MAX_READ_BYTES = int(os.environ.get("MCP_FILESYSTEM_MAX_READ_BYTES", "1048576"))

mcp = FastMCP(
    name="filesystem",
    instructions=(
        "Secure filesystem server with path sandboxing. "
        "All operations are restricted to the configured root directory."
    ),
    host="0.0.0.0",
    port=8765,
    streamable_http_path="/mcp",
)


def _resolve_path(raw_path: str) -> Path:
    if not raw_path or raw_path.strip() in {".", "/"}:
        candidate = ROOT
    else:
        candidate = (ROOT / raw_path).resolve()

    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("Path escapes MCP filesystem root") from exc

    return candidate


def _ensure_write_allowed() -> None:
    if READ_ONLY:
        raise PermissionError("Filesystem server is running in read-only mode")


@mcp.tool()
def get_root() -> str:
    """Return the absolute sandbox root path."""
    return str(ROOT)


@mcp.tool()
def list_directory(path: str = ".") -> list[dict]:
    """List files and folders under a directory relative to the sandbox root."""
    target = _resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        rel = child.relative_to(ROOT)
        entries.append(
            {
                "name": child.name,
                "path": str(rel),
                "type": "directory" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return entries


@mcp.tool()
def read_text_file(path: str) -> str:
    """Read a UTF-8 text file from the sandbox."""
    target = _resolve_path(path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    raw = target.read_bytes()
    if len(raw) > MAX_READ_BYTES:
        raise ValueError(
            f"File exceeds max read size ({MAX_READ_BYTES} bytes): {path}"
        )
    return raw.decode("utf-8")


@mcp.tool()
def write_text_file(path: str, content: str, create_parents: bool = True) -> dict:
    """Create or overwrite a UTF-8 text file inside the sandbox."""
    _ensure_write_allowed()
    target = _resolve_path(path)

    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(content, encoding="utf-8")
    return {"path": str(target.relative_to(ROOT)), "bytes_written": len(content.encode("utf-8"))}


@mcp.tool()
def append_text_file(path: str, content: str, create_parents: bool = True) -> dict:
    """Append UTF-8 text to an existing file or create a new one."""
    _ensure_write_allowed()
    target = _resolve_path(path)

    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("a", encoding="utf-8") as f:
        f.write(content)

    return {"path": str(target.relative_to(ROOT)), "bytes_appended": len(content.encode("utf-8"))}


@mcp.tool()
def make_directory(path: str, exist_ok: bool = True) -> dict:
    """Create a directory inside the sandbox."""
    _ensure_write_allowed()
    target = _resolve_path(path)
    target.mkdir(parents=True, exist_ok=exist_ok)
    return {"path": str(target.relative_to(ROOT))}


@mcp.tool()
def move_path(src: str, dst: str, overwrite: bool = False) -> dict:
    """Move or rename a file/folder within the sandbox."""
    _ensure_write_allowed()
    src_path = _resolve_path(src)
    dst_path = _resolve_path(dst)

    if not src_path.exists():
        raise FileNotFoundError(f"Source does not exist: {src}")
    if dst_path.exists() and not overwrite:
        raise FileExistsError(f"Destination exists: {dst}")

    if dst_path.exists() and overwrite:
        if dst_path.is_dir():
            shutil.rmtree(dst_path)
        else:
            dst_path.unlink()

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.rename(dst_path)
    return {
        "from": str(src_path.relative_to(ROOT)),
        "to": str(dst_path.relative_to(ROOT)),
    }


@mcp.tool()
def delete_path(path: str, recursive: bool = True) -> dict:
    """Delete a file or directory in the sandbox."""
    _ensure_write_allowed()
    target = _resolve_path(path)

    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()

    return {"deleted": str(target.relative_to(ROOT))}


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
