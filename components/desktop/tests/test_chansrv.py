"""The newest Remote Desktop connection gets xrdp-chansrv, even while an older one is still open."""
import asyncio
import os

from odd1_desktop.chansrv import Proxy


class FakeChansrv:
    """Like xrdp-chansrv: one connection at a time, its listening socket gone while it has one."""

    def __init__(self, path):
        self.path, self.received, self.connections = path, [], 0

    async def listen(self):
        self.server = await asyncio.start_unix_server(self.serve, path=self.path)

    async def serve(self, reader, writer):
        self.connections += 1
        self.server.close()
        os.unlink(self.path)
        while data := await reader.read(100):
            self.received.append(data)
            writer.write(b"ack:" + data)
            await writer.drain()
        writer.close()
        await asyncio.sleep(0.2)  # chansrv resets before it listens again
        await self.listen()


async def _exchange(path, message):
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(message)
    await writer.drain()
    return reader, writer, await asyncio.wait_for(reader.read(100), 5)


def test_chansrv_follows_newest_connection(tmp_path):
    async def scenario():
        chansrv = FakeChansrv(str(tmp_path / "xrdp_chansrv_socket_0"))
        await chansrv.listen()
        listen = str(tmp_path / "odd1_chansrv_socket_0")
        server = await Proxy(listen, chansrv.path, wait=5).serve()
        assert os.stat(listen).st_mode & 0o777 == 0o660  # xrdp connects through the group
        old_reader, _, reply = await _exchange(listen, b"18:19")
        assert reply == b"ack:18:19"
        # The person connects again while the 18:19 connection is still open: the new one gets chansrv.
        _, new_writer, reply = await _exchange(listen, b"00:02")
        assert reply == b"ack:00:02"
        assert chansrv.connections == 2
        assert await asyncio.wait_for(old_reader.read(100), 5) == b""  # the older link was closed
        new_writer.close()
        server.close()

    asyncio.run(scenario())
