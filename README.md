# mcp-show-on-kindle

An MCP server with a single tool, show(file_path), which copies a file to a
Kindle running KOReader and opens it on the screen.

The Kindle is located on the local network by its KOReader SSH banner, the file
is copied over the SSH server plugin, and the running KOReader is told to open
it through the HTTP inspector plugin. The inspector is reached over the same
SSH connection, so the only thing on the wire is SSH.

## Setup on the Kindle

Enable two KOReader plugins on autostart:

  * SSH server: port 2222, your public key in
    /mnt/us/koreader/settings/SSH/authorized_keys
  * HTTP inspector: port 8080

The inspector has no authentication, so block it from the network:

    ssh -p 2222 root@KINDLE "iptables -I INPUT 1 -p tcp --dport 8080 ! -i lo -j DROP"

Add the same line near the top of /mnt/us/koreader/koreader.sh to apply it on
every start.

## Setup on the computer

    uv sync
    KINDLE_ALLOWED_DIRECTORIES=/some/dir uv run mcp-show-on-kindle

The server speaks stdio by default. For a long-running service, use
--transport streamable-http --port 7004 and this client config:

    {"mcpServers": {"show_on_kindle": {"type": "http", "url": "http://localhost:7004/mcp"}}}

## Configuration

    KINDLE_ALLOWED_DIRECTORIES  directories show() may read from. Required.
    KINDLE_HOST                 Kindle IP. Default: scan the /24, cache the result.
    KINDLE_SSH_PORT             default 2222
    KINDLE_HTTP_PORT            default 8080
    KINDLE_DIR                  target directory, default /mnt/us/documents/llm
    KINDLE_KNOWN_HOSTS          pinned host key, default ~/.config/mcp-show-on-kindle/known_hosts

## Security

The banner only locates the Kindle. Authentication is the SSH host key, pinned
on first contact under a fixed alias; a different host presenting the same
banner fails. File reads are restricted to KINDLE_ALLOWED_DIRECTORIES, and
filenames are reduced to [A-Za-z0-9._-] before they are used in a remote
command. With the iptables rule in place the Kindle exposes nothing but
public-key SSH. Use a separate passphrase-less key that grants access to
nothing else.

## Tests

    make tests-run

ssh and sockets are mocked; the tests run without a Kindle.

## License

MPL 2.0
