# mcp-show-on-kindle

MCP server with one tool, show(file_path). Copies a file to a Kindle running
KOReader and opens it on screen.

It finds the Kindle on the LAN by its KOReader SSH banner, copies the file
over the SSH plugin, then tells KOReader to open it through the HTTP
inspector plugin. The inspector is called over the same SSH connection, so
only SSH is on the wire.

## Kindle setup

Enable the SSH server (port 2222) and HTTP inspector (port 8080) plugins in
KOReader, on autostart.

Install a key:

    ssh-keygen -t ed25519 -N "" -f ~/.config/mcp-show-on-kindle/id_ed25519
    cat ~/.config/mcp-show-on-kindle/id_ed25519.pub | \
      ssh -p 2222 root@KINDLE "cat >> /mnt/us/koreader/settings/SSH/authorized_keys"

The inspector has no authentication. Block it from the network:

    ssh -p 2222 root@KINDLE "iptables -I INPUT 1 -p tcp --dport 8080 ! -i lo -j DROP"

Put the same line near the top of /mnt/us/koreader/koreader.sh so it survives
a reboot.

## Client setup

    {
      "mcpServers": {
        "show_on_kindle": {
          "command": "uvx",
          "args": ["--from", "mcp-show-on-kindle==2026.10.15.202733",
                   "--refresh-package", "mcp-show-on-kindle", "mcp-show-on-kindle"],
          "env": {
            "KINDLE_ALLOWED_DIRECTORIES": "/some/dir"
          }
        }
      }
    }

Or run it as a service with --transport streamable-http --port 7004 and use

    {"mcpServers": {"show_on_kindle": {"type": "http", "url": "http://localhost:7004/mcp"}}}

From a checkout: uv sync, then "command": "uv", "args": ["run", "--directory",
"/path/to/checkout", "mcp-show-on-kindle"].

## Configuration

    KINDLE_ALLOWED_DIRECTORIES  directories show() may read from. Required.
    KINDLE_HOST                 Kindle IP. Default: scan the /24, cache the result.
    KINDLE_SSH_PORT             default 2222
    KINDLE_HTTP_PORT            default 8080
    KINDLE_DIR                  target directory, default /mnt/us/documents/llm
    KINDLE_SSH_KEY              private key to use. Default: whatever ssh picks.
    KINDLE_KNOWN_HOSTS          pinned host key, default ~/.config/mcp-show-on-kindle/known_hosts

## Notes

The Kindle's wifi sleeps hard, so the subnet scan runs twice; the first pass
mostly just wakes it up. Dropbear has no scp, so the file goes over stdin
with cat. Opening a document restarts KOReader's UI, and the inspector with
it, so the open request always fails; the server polls afterwards to see
what is open.

The banner only finds the Kindle. What authenticates it is the SSH host key,
pinned on first contact under a fixed alias. Reads are limited to
KINDLE_ALLOWED_DIRECTORIES, filenames are reduced to [A-Za-z0-9._-] before
they go into a remote command, and with the iptables rule the Kindle exposes
nothing but ssh.

## Tests

    make tests-run

ssh and sockets are mocked.

## My Other LLM Projects

- **[MCP Alchemy](https://github.com/runekaagaard/mcp-alchemy)** - Connect Claude Desktop to databases for exploring schema and running SQL.
- **[MCP Redmine](https://github.com/runekaagaard/mcp-redmine)** - Let Claude Desktop manage your Redmine projects and issues.
- **[MCP Notmuch Sendmail](https://github.com/runekaagaard/mcp-notmuch-sendmail)** - Email assistant for Claude Desktop using notmuch.
- **[Diffpilot](https://github.com/runekaagaard/diffpilot)** - Multi-column git diff viewer with file grouping and tagging.
- **[Claude Local Files](https://github.com/runekaagaard/claude-local-files)** - Access local files in Claude Desktop artifacts.

## License

MPL 2.0
