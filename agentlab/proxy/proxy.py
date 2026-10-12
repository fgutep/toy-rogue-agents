"""A small forward proxy that enforces egress and binds identity (doc 01 §3.1, doc 03 §1.4).

This is the single chokepoint for all agent outbound traffic. It provides the
*mechanism*; the experiment config provides the *allowlist* (doc 01 §3.1). It:

* **fails closed** — a destination host not on the allowlist is refused, for both
  plain HTTP requests and HTTPS `CONNECT` tunnels;
* **binds identity at the transport layer** — for plain-HTTP requests to the
  internal services (Armory, oracle, honeypot), it injects an `X-Agent-Identity`
  header derived from the *client's* network identity (reverse DNS of the source
  IP on the Docker bridge → container name → agent id), after **stripping** any
  value the agent set. The agent cannot forge or even observe this (doc 03
  §1.4). HTTPS tunnels are allowlisted but not inspected (they are the external
  model APIs, which are not identity-sensitive).

Deliberately minimal and dependency-free (stdlib asyncio) so it is easy to audit
— the thing enforcing containment should be small enough to read in full.

Run inside the sidecar container:
    python -m agentlab.proxy.proxy

Environment:
    AGENTLAB_EGRESS_ALLOW   comma-separated allowlisted hostnames
    AGENTLAB_PROXY_PORT     listen port (default 3128)
    AGENTLAB_IDENTITY_MAP   optional JSON {container_name_prefix: agent_id}
"""

from __future__ import annotations

import asyncio
import os
import socket

ALLOW: set[str] = set()
IDENTITY_SUFFIX = ""  # kept simple; see _identity_for


def _host_allowed(host: str) -> bool:
    host = host.split(":")[0].lower()
    return host in ALLOW


def _identity_for(peername: tuple) -> str:
    """Resolve the client's network identity to an agent id (doc 03 §1.4).

    On a Docker bridge network, reverse DNS of the source IP yields the
    container's name (e.g. ``run-sample-agent-agent-01-1``); we extract the
    ``agent-<id>`` portion. Falls back to the raw IP so identity is never blank
    and never taken from the agent's own claim.
    """
    ip = peername[0] if peername else "unknown"
    try:
        name = socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip
    for part in name.split("."):
        idx = part.find("agent-")
        if idx != -1:
            return part[idx:]
    return name


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def _handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        request_line = await client_reader.readline()
        if not request_line:
            client_writer.close()
            return
        parts = request_line.decode("latin1").split()
        if len(parts) < 3:
            client_writer.close()
            return
        method, target, _version = parts[0], parts[1], parts[2]
        peer = client_writer.get_extra_info("peername")

        # Read the rest of the request headers.
        headers: list[tuple[str, str]] = []
        while True:
            line = await client_reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin1").partition(":")
            headers.append((k.strip(), v.strip()))

        if method.upper() == "CONNECT":
            await _handle_connect(target, client_reader, client_writer)
            return

        await _handle_plain(method, target, headers, peer, client_reader, client_writer)
    except Exception:
        try:
            client_writer.close()
        except Exception:
            pass


async def _handle_connect(target: str, cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    host, _, port = target.partition(":")
    if not _host_allowed(host):
        cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    try:
        rr, rw = await asyncio.open_connection(host, int(port or 443))
    except Exception:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await cw.drain()
    await asyncio.gather(_pipe(cr, rw), _pipe(rr, cw))


async def _handle_plain(method, target, headers, peer, cr, cw) -> None:
    # target is an absolute URI for proxied requests: http://host:port/path
    from urllib.parse import urlsplit

    u = urlsplit(target)
    host, port = u.hostname or "", u.port or 80
    if not _host_allowed(host):
        cw.write(b"HTTP/1.1 403 Forbidden\r\n\r\nblocked by egress policy\n")
        await cw.drain()
        cw.close()
        return

    identity = _identity_for(peer)
    # Strip any agent-supplied identity header; inject the transport-derived one.
    filtered = [(k, v) for (k, v) in headers if k.lower() != "x-agent-identity"]
    filtered.append(("X-Agent-Identity", identity))

    path = u.path + (("?" + u.query) if u.query else "")
    try:
        rr, rw = await asyncio.open_connection(host, port)
    except Exception:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await cw.drain()
        cw.close()
        return

    body = b""
    length = next((int(v) for (k, v) in headers if k.lower() == "content-length"), 0)
    if length:
        body = await cr.readexactly(length)

    out = [f"{method} {path or '/'} HTTP/1.1"]
    out += [f"{k}: {v}" for (k, v) in filtered]
    rw.write(("\r\n".join(out) + "\r\n\r\n").encode("latin1") + body)
    await rw.drain()
    await asyncio.gather(_pipe(rr, cw), _pipe(cr, rw))


async def main() -> None:
    global ALLOW
    raw = os.environ.get("AGENTLAB_EGRESS_ALLOW", "").split(",")
    ALLOW = {h.strip().lower() for h in raw if h.strip()}
    port = int(os.environ.get("AGENTLAB_PROXY_PORT", "3128"))
    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
