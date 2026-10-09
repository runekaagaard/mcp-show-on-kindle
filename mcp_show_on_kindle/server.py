import os, pathlib, re, socket, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.logging import get_logger

### Constants ###

VERSION = "2026.10.09.000000"

# Constants from environment
KINDLE_HOST = os.environ.get("KINDLE_HOST", "")
KINDLE_SSH_PORT = int(os.environ.get("KINDLE_SSH_PORT", "2222"))
KINDLE_HTTP_PORT = int(os.environ.get("KINDLE_HTTP_PORT", "8080"))
KINDLE_DIR = os.environ.get("KINDLE_DIR", "/mnt/us/documents/llm")

# Directories the model may read files from (secure by default - disabled if not set)
KINDLE_ALLOWED_DIRECTORIES = [
    pathlib.Path(d.strip()).expanduser().resolve()
    for d in os.environ.get("KINDLE_ALLOWED_DIRECTORIES", "").split(",")
    if d.strip()
]

CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", str(pathlib.Path.home() / ".cache"))) \
    / "mcp-show-on-kindle" / "host"

# The Kindle's SSH host key, pinned on first contact under a fixed alias so it holds
# across IP changes.
KNOWN_HOSTS = pathlib.Path(
    os.environ.get("KINDLE_KNOWN_HOSTS",
                   str(pathlib.Path.home() / ".config" / "mcp-show-on-kindle" / "known_hosts")))


### Kindle discovery ###

# E-reader wifi power saving delays the first connection; 1s is too short. The sweep
# runs 64-wide so 3s does not make it slow.
def port_open(ip, port, timeout=3.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_banner(ip, port, timeout=3.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            return s.recv(64).decode(errors="replace")
    except OSError:
        return ""


def is_kindle(ip):
    """KOReader's SSH banner is "SSH-2.0-dropbear_... (KOReader)". This locates the
    Kindle; the pinned host key is what authenticates it."""
    return "koreader" in read_banner(ip, KINDLE_SSH_PORT).lower()


def own_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def find_kindle():
    """Env override, then cached IP, then sweep the /24. Returns (ip, error),
    one of them always None."""
    if KINDLE_HOST:
        return KINDLE_HOST, None

    if CACHE.is_file():
        cached = CACHE.read_text().strip()
        if cached and is_kindle(cached):
            return cached, None

    src = own_ip()
    if not src:
        return None, "No network route - is this machine on Wi-Fi?"

    net = src.rsplit(".", 1)[0]
    candidates = [f"{net}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as ex:
        open_ips = [ip for ip, ok in zip(candidates, ex.map(
            lambda ip: port_open(ip, KINDLE_SSH_PORT), candidates)) if ok]

    for ip in open_ips:
        if is_kindle(ip):
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(ip)
            return ip, None

    return None, (f"Kindle not found on {net}.0/24 - is it awake, running KOReader with the "
                  "SSH plugin, and on the same Wi-Fi as this machine?")


### Sending and opening ###

def ssh(host, command, stdin=None):
    KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["ssh", "-p", str(KINDLE_SSH_PORT), "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=accept-new",
         "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
         "-o", "HostKeyAlias=kindle",
         f"root@{host}", command],
        input=stdin, capture_output=True, timeout=60)


def validate_path(file_path):
    """Returns (None, resolved_path) on success, (error_message, None) on failure."""
    if not KINDLE_ALLOWED_DIRECTORIES:
        return "File sending disabled: KINDLE_ALLOWED_DIRECTORIES not configured", None
    path = pathlib.Path(file_path).expanduser()
    if not path.is_absolute():
        return f"Path must be absolute, got: {file_path}", None
    path = path.resolve()
    if not any(path.is_relative_to(allowed) for allowed in KINDLE_ALLOWED_DIRECTORIES):
        return f"Path not in KINDLE_ALLOWED_DIRECTORIES: {file_path}", None
    if not path.is_file():
        return f"File not found: {file_path}", None
    return None, path


def open_on_kindle(host, kindle_path):
    """Ask KOReader to open the file via its HTTP inspector, reached through the SSH
    session against loopback.

    ReaderUI has switchDocument, FileManager has openFile; the wrong one returns 404
    harmlessly, so both are called. A successful open tears down the UI and the
    inspector with it, so the calls themselves prove nothing - the poll afterwards
    does."""
    base = f"http://127.0.0.1:{KINDLE_HTTP_PORT}/koreader/ui"
    arg = quote(f'"{kindle_path}"', safe="")

    for method in ["switchDocument", "openFile"]:
        ssh(host, f"wget -q -O- -T 15 '{base}/{method}/{arg}/'")

    for _ in range(5):
        time.sleep(2)
        result = ssh(host, f"wget -q -O- -T 5 '{base}/document/file'")
        if result.returncode == 0 and result.stdout.decode(errors="replace").strip().strip('"') == kindle_path:
            return None
    return f"Copied to {kindle_path}, but KOReader did not open it"


### Tools ###

mcp = MCPServer("show-on-kindle", version=VERSION)
get_logger(__name__).info(f"Starting show-on-kindle version {VERSION}")


@mcp.tool()
def show(file_path: str) -> str:
    """Copy a file to the Kindle and show it on screen immediately in KOReader.

    The Kindle must be awake, running KOReader, and on the same Wi-Fi as this machine.
    The first call after changing networks is slow (it sweeps the subnet for the Kindle).

    Args:
        file_path: Absolute path to the file (must be inside KINDLE_ALLOWED_DIRECTORIES)

    Returns:
        A confirmation, or an error message explaining what to fix
    """
    error, path = validate_path(file_path)
    if error:
        return error

    host, error = find_kindle()
    if error:
        return error

    # The name is used in a remote shell command, so restrict it to a safe charset.
    name = re.sub(r"[^A-Za-z0-9._-]", "_", path.name) or "file"
    kindle_path = f"{KINDLE_DIR}/{name}"
    try:
        ssh(host, f"mkdir -p '{KINDLE_DIR}'")
        result = ssh(host, f"cat > '{kindle_path}'", stdin=path.read_bytes())
        if result.returncode != 0:
            return f"Copy failed: {result.stderr.decode(errors='replace').strip()}"
    except subprocess.TimeoutExpired:
        return f"SSH to the Kindle at {host} timed out"

    error = open_on_kindle(host, kindle_path)
    if error:
        return error
    return f"Now showing {kindle_path} on the Kindle"


def main():
    """Main entry point for the mcp-show-on-kindle package."""
    import argparse
    parser = argparse.ArgumentParser(description="MCP Show-on-Kindle Server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio",
                        help="Transport type (default: stdio). streamable-http is the recommended HTTP "
                             "transport, sse is supported for legacy clients.")
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transports (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7004, help="Port for HTTP transports (default: 7004)")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
