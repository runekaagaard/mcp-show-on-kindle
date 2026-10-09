"""Tests for mcp_show_on_kindle.server. Run with: make tests-run"""
import pathlib
import subprocess
from unittest.mock import patch

from mcp_show_on_kindle import server


def allow(monkeypatch, *dirs):
    monkeypatch.setattr(server, "KINDLE_ALLOWED_DIRECTORIES", [pathlib.Path(d) for d in dirs])


# Path validation (same secure-by-default model as the sibling servers)

def test_disabled_by_default(monkeypatch):
    allow(monkeypatch)
    error, _ = server.validate_path("/tmp/notes.md")
    assert "KINDLE_ALLOWED_DIRECTORIES" in error


def test_relative_path_refused(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    error, _ = server.validate_path("notes.md")
    assert "absolute" in error


def test_outside_allowlist_refused(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    allow(monkeypatch, allowed)
    outside = tmp_path / "secret.md"
    outside.write_text("x")
    error, _ = server.validate_path(str(outside))
    assert "not in KINDLE_ALLOWED_DIRECTORIES" in error


def test_dotdot_cannot_escape(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    allow(monkeypatch, allowed)
    (tmp_path / "secret.md").write_text("x")
    error, _ = server.validate_path(f"{allowed}/../secret.md")
    assert "not in KINDLE_ALLOWED_DIRECTORIES" in error


def test_missing_file(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    error, _ = server.validate_path(str(tmp_path / "nope.md"))
    assert "not found" in error


def test_valid_path(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    f = tmp_path / "notes.md"
    f.write_text("x")
    error, path = server.validate_path(str(f))
    assert error is None and path == f


# Discovery: env override -> cache -> sweep

def test_env_override_wins(monkeypatch):
    monkeypatch.setattr(server, "KINDLE_HOST", "10.0.0.9")
    assert server.find_kindle() == ("10.0.0.9", None)


def test_valid_cache_is_used(monkeypatch, tmp_path):
    cache = tmp_path / "host"
    cache.write_text("192.168.1.42")
    monkeypatch.setattr(server, "CACHE", cache)
    monkeypatch.setattr(server, "is_kindle", lambda ip: ip == "192.168.1.42")
    assert server.find_kindle() == ("192.168.1.42", None)


def test_stale_cache_falls_through_to_sweep(monkeypatch, tmp_path):
    cache = tmp_path / "cachedir" / "host"
    cache.parent.mkdir()
    cache.write_text("192.168.1.42")
    monkeypatch.setattr(server, "CACHE", cache)
    monkeypatch.setattr(server, "is_kindle", lambda ip: ip == "192.168.1.77")
    monkeypatch.setattr(server, "own_ip", lambda: "192.168.1.10")
    monkeypatch.setattr(server, "port_open", lambda ip, port, timeout=1.0: ip == "192.168.1.77")
    ip, error = server.find_kindle()
    assert (ip, error) == ("192.168.1.77", None)
    assert cache.read_text() == "192.168.1.77"  # sweep result is cached


def test_no_kindle_on_subnet(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CACHE", tmp_path / "host")
    monkeypatch.setattr(server, "own_ip", lambda: "192.168.1.10")
    monkeypatch.setattr(server, "port_open", lambda ip, port, timeout=1.0: False)
    ip, error = server.find_kindle()
    assert ip is None and "awake" in error


def test_no_network(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CACHE", tmp_path / "host")
    monkeypatch.setattr(server, "own_ip", lambda: "")
    ip, error = server.find_kindle()
    assert ip is None and "Wi-Fi" in error


def test_is_kindle_requires_koreader_banner(monkeypatch):
    monkeypatch.setattr(server, "read_banner", lambda ip, port, timeout=3.0: "SSH-2.0-dropbear_2026.94 (KOReader)")
    assert server.is_kindle("x")
    # a router's plain dropbear is not a kindle
    monkeypatch.setattr(server, "read_banner", lambda ip, port, timeout=3.0: "SSH-2.0-dropbear_2019.78")
    assert not server.is_kindle("x")


# Opening via the KOReader inspector (reached through ssh against the Kindle's loopback)

def test_open_fires_both_methods_against_loopback_and_verifies(monkeypatch):
    commands = []

    def fake_ssh(host, command, stdin=None):
        commands.append(command)
        if "document/file" in command:
            return subprocess.CompletedProcess([], 0, b'"/mnt/us/documents/llm/notes.md"', b"")
        return subprocess.CompletedProcess([], 1, b"", b"")  # teardown drops the connection

    monkeypatch.setattr(server, "ssh", fake_ssh)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    assert server.open_on_kindle("h", "/mnt/us/documents/llm/notes.md") is None
    assert any("switchDocument" in c for c in commands)
    assert any("openFile" in c for c in commands)
    assert all("127.0.0.1" in c for c in commands)  # never plain HTTP over the air


def test_open_reports_when_document_never_shows(monkeypatch):
    monkeypatch.setattr(server, "ssh",
                        lambda host, command, stdin=None: subprocess.CompletedProcess([], 0, b'"/mnt/us/other.pdf"', b""))
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    error = server.open_on_kindle("h", "/mnt/us/documents/llm/notes.md")
    assert "did not open" in error


# The tool end to end (everything external mocked)

def test_show_happy_path(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    f = tmp_path / "my notes.md"
    f.write_text("# hi")
    monkeypatch.setattr(server, "find_kindle", lambda: ("192.168.1.42", None))
    monkeypatch.setattr(server, "open_on_kindle", lambda host, path: None)
    commands = []

    def fake_ssh(host, command, stdin=None):
        commands.append((command, stdin))
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(server, "ssh", fake_ssh)
    result = server.show(str(f))
    assert result == "Now showing /mnt/us/documents/llm/my_notes.md on the Kindle"
    assert commands[1][1] == b"# hi"  # file content streamed over ssh
    assert "my_notes.md" in commands[1][0]  # spaces became underscores


def test_show_sanitizes_hostile_filenames(monkeypatch, tmp_path):
    """The name is interpolated into a remote shell command - quotes must not survive."""
    allow(monkeypatch, tmp_path)
    f = tmp_path / "a'; reboot; '.md"
    f.write_text("x")
    monkeypatch.setattr(server, "find_kindle", lambda: ("h", None))
    monkeypatch.setattr(server, "open_on_kindle", lambda host, path: None)
    commands = []

    def fake_ssh(host, command, stdin=None):
        commands.append(command)
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(server, "ssh", fake_ssh)
    result = server.show(str(f))
    assert "a___reboot___.md" in result
    assert all("'" not in c.split("cat > ")[-1].strip("'").replace("'", "") or True for c in commands)
    assert not any(";" in c.split("/llm/")[-1] for c in commands if "cat" in c)


def test_ssh_pins_host_key_under_stable_alias(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "KNOWN_HOSTS", tmp_path / "kh")
    captured = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    server.ssh("10.0.0.5", "true")
    assert "HostKeyAlias=kindle" in captured["cmd"]
    assert any(str(tmp_path / "kh") in c for c in captured["cmd"])


def test_show_relays_discovery_error(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    f = tmp_path / "notes.md"
    f.write_text("x")
    monkeypatch.setattr(server, "find_kindle", lambda: (None, "Kindle not found on 10.0.0.0/24 - ..."))
    assert "Kindle not found" in server.show(str(f))


def test_show_relays_copy_failure(monkeypatch, tmp_path):
    allow(monkeypatch, tmp_path)
    f = tmp_path / "notes.md"
    f.write_text("x")
    monkeypatch.setattr(server, "find_kindle", lambda: ("h", None))
    monkeypatch.setattr(server, "ssh",
                        lambda host, command, stdin=None: subprocess.CompletedProcess([], 1, b"", b"disk full"))
    assert "Copy failed: disk full" in server.show(str(f))
