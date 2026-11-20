"""Per-session HTTP forward proxy. Resolve once and connect only to public IPv4."""

import asyncio
import base64
import ipaddress
import os
import socket
from urllib.parse import unquote, urlsplit


async def relay(reader, writer):
    while chunk := await asyncio.wait_for(reader.read(65536), 120):
        writer.write(chunk)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


async def connect_public(ip, port):
    # Some installations route all public TLS traffic through a corporate proxy.
    # CONNECT to the already validated numeric address, never resolve it again.
    configured = (
        os.getenv("FAMSUN_EGRESS_UPSTREAM")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
    )
    if not configured:
        return await asyncio.open_connection(str(ip), port)
    proxy = urlsplit(configured)
    if proxy.scheme not in {"http", "https"} or not proxy.hostname:
        raise ValueError("invalid upstream proxy")
    upstream, output = await asyncio.open_connection(
        proxy.hostname,
        proxy.port or (443 if proxy.scheme == "https" else 80),
        ssl=True if proxy.scheme == "https" else None,
    )
    try:
        destination = f"{ip}:{port}"
        headers = [f"CONNECT {destination} HTTP/1.1", f"Host: {destination}"]
        if proxy.username is not None:
            creds = (
                unquote(proxy.username) + ":" + unquote(proxy.password or "")
            ).encode()
            headers.append(
                "Proxy-Authorization: Basic " + base64.b64encode(creds).decode()
            )
        output.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        await output.drain()
        reply = await upstream.readuntil(b"\r\n\r\n")
        if reply.split(b" ", 2)[1] != b"200":
            raise ConnectionError("upstream rejected destination")
        return upstream, output
    except BaseException:
        output.close()
        raise


async def handle(client, output):
    remote = None
    response_started = False
    try:
        request = await asyncio.wait_for(client.readuntil(b"\r\n\r\n"), 10)
        lines = request[:-4].split(b"\r\n")
        method, target, version = lines[0].decode("ascii").split(" ")
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ValueError("unsupported protocol")
        tunnel = method == "CONNECT"
        address = urlsplit("//" + target if tunnel else target)
        if (
            (not tunnel and address.scheme != "http")
            or address.username
            or address.password
        ):
            raise ValueError("invalid proxy target")
        port = address.port or (443 if tunnel else 80)
        if not address.hostname or port not in {80, 443}:
            raise ValueError("invalid destination")
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                address.hostname, port, family=socket.AF_INET, type=socket.SOCK_STREAM
            ),
            8,
        )
        ips = [ipaddress.ip_address(answer[4][0]) for answer in answers]
        if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
            raise ValueError("destination denied")
        upstream, remote = await asyncio.wait_for(connect_public(ips[0], port), 10)
        if tunnel:
            output.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            resource = (address.path or "/") + (
                "?" + address.query if address.query else ""
            )
            keep = [
                line
                for line in lines[1:]
                if line.split(b":", 1)[0].lower()
                not in {
                    b"host",
                    b"proxy-authorization",
                    b"proxy-connection",
                    b"connection",
                }
            ]
            start = (
                f"{method} {resource} {version}\r\nHost: {address.netloc}\r\n".encode(
                    "ascii"
                )
            )
            remote.write(start + b"\r\n".join([*keep, b"Connection: close", b"", b""]))
            await remote.drain()
        response_started = True
        async with asyncio.TaskGroup() as tasks:
            upload = tasks.create_task(relay(client, remote))
            await relay(upstream, output)
            upload.cancel()
    except (
        OSError,
        ValueError,
        IndexError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        ExceptionGroup,
    ):
        if not response_started:
            output.write(
                b"HTTP/1.1 403 Proxy request denied\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
            )
    finally:
        if remote:
            remote.close()
        output.close()


async def main():
    server = await asyncio.start_server(
        handle, socket.gethostbyname("egress"), 3128, limit=16384, backlog=64
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
