import asyncio


async def copy_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection("::1", 5555)
    except Exception:
        writer.close()
        await writer.wait_closed()
        return

    await asyncio.gather(
        copy_stream(reader, upstream_writer),
        copy_stream(upstream_reader, writer),
        return_exceptions=True,
    )


async def main() -> None:
    server = await asyncio.start_server(relay, "127.0.0.1", 5557)
    print("relay_ready=127.0.0.1:5557->[::1]:5555", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
