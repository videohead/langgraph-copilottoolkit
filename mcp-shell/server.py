import os
import shlex
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

ROOT = Path(os.environ.get("MCP_SHELL_ROOT", "/workspace-data")).resolve()
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("MCP_SHELL_TIMEOUT_SECONDS", "15"))
MAX_OUTPUT_BYTES = int(os.environ.get("MCP_SHELL_MAX_OUTPUT_BYTES", "65536"))
ALLOWLIST = tuple(
    p.strip()
    for p in os.environ.get(
        "MCP_SHELL_ALLOWLIST",
        "ls,cat,head,tail,pwd,echo,find,grep,rg,sed,awk,wc,stat,du,df,mkdir,cp,mv,touch",
    ).split(",")
    if p.strip()
)

mcp = FastMCP(
    name="shell",
    instructions=(
        "Sandboxed shell command server with command allowlist and path sandboxing. "
        "All commands run inside the configured root directory."
    ),
    host="0.0.0.0",
    port=8770,
    streamable_http_path="/mcp",
)


@mcp.custom_route("/", methods=["GET"])
async def root_status(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "name": "shell",
            "status": "ok",
            "transport": "streamable-http",
            "mcp_endpoint": "/mcp",
            "health_endpoint": "/health",
            "root": str(ROOT),
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "max_output_bytes": MAX_OUTPUT_BYTES,
            "allowlist": list(ALLOWLIST),
        }
    )


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _normalize_cwd(raw_cwd: str | None) -> Path:
    if not raw_cwd or raw_cwd.strip() in {"", ".", "/"}:
        target = ROOT
    else:
        candidate = Path(raw_cwd)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        target = candidate.resolve()

    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("cwd escapes MCP shell root") from exc

    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"cwd is not a directory: {target}")

    return target


def _parse_and_validate_command(command: str) -> tuple[list[str], str]:
    parts = shlex.split(command)
    if not parts:
        raise ValueError("Empty command")

    binary = Path(parts[0]).name
    if binary not in ALLOWLIST:
        raise PermissionError(
            f"Command '{binary}' is blocked by allowlist. Allowed: {', '.join(ALLOWLIST)}"
        )

    return parts, binary


def _trim_output(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= MAX_OUTPUT_BYTES:
        return text, False
    clipped = raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return clipped, True


@mcp.tool()
def get_shell_policy() -> dict:
    """Return shell policy metadata including allowlisted commands."""
    return {
        "root": str(ROOT),
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "allowlist": list(ALLOWLIST),
    }


@mcp.tool()
def run_shell(command: str, cwd: str = ".", timeout_seconds: int | None = None) -> dict:
    """Run an allowlisted shell command in the sandbox and return stdout/stderr."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")

    command = command.strip()
    argv, binary = _parse_and_validate_command(command)
    target_cwd = _normalize_cwd(cwd)
    timeout = timeout_seconds if isinstance(timeout_seconds, int) and timeout_seconds > 0 else DEFAULT_TIMEOUT_SECONDS

    try:
        result = subprocess.run(
            argv,
            shell=False,
            cwd=str(target_cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout, stdout_truncated = _trim_output(result.stdout)
        stderr, stderr_truncated = _trim_output(result.stderr)
        return {
            "command": command,
            "binary": binary,
            "cwd": str(target_cwd),
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _trim_output(exc.stdout or "")
        stderr, stderr_truncated = _trim_output(exc.stderr or "")
        return {
            "command": command,
            "binary": binary,
            "cwd": str(target_cwd),
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timed_out": True,
        }


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
