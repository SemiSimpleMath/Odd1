"""The newest Remote Desktop connection gets xrdp-chansrv (clipboard, shared drives), even while an older one is open.

xrdp-chansrv serves one xrdp connection at a time: while it has one, it removes its listening socket, so a second
Remote Desktop connection to the same screen got no channels (no clipboard, ~/Windows empty) until the first ended.
xrdp connects here instead (base.nix: `chansrvport`); each new connection closes the previous one's link, chansrv
resets and listens again, and the new connection is spliced through to it.
"""
import asyncio
import os
import sys


class Proxy:
    def __init__(self, listen, chansrv, wait=15.0):
        self.listen, self.chansrv, self.wait = listen, chansrv, wait
        self.current = None  # the newest connection's task

    async def serve(self):
        if os.path.exists(self.listen):
            os.unlink(self.listen)
        server = await asyncio.start_unix_server(self.connected, path=self.listen)
        os.chmod(self.listen, 0o660)  # xrdp (group xrdp, the directory's) connects as itself
        return server

    async def connected(self, reader, writer):
        previous, self.current = self.current, asyncio.current_task()
        if previous is not None:
            previous.cancel()  # its link to chansrv closes, so chansrv resets and listens again
            await asyncio.gather(previous, return_exceptions=True)
        try:
            upstream = await self.reach_chansrv()
            if upstream is None:
                print(f"odd1-desktop chansrv: {self.chansrv} did not listen within {self.wait}s", file=sys.stderr)
                return
            up_reader, up_writer = upstream
            try:
                await _first(_pipe(reader, up_writer), _pipe(up_reader, writer))
            finally:
                up_writer.close()
        finally:
            writer.close()
            if self.current is asyncio.current_task():
                self.current = None

    async def reach_chansrv(self):
        """chansrv listens again a moment after its last connection closed."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait
        while True:
            try:
                return await asyncio.open_unix_connection(self.chansrv)
            except OSError:
                if loop.time() > deadline:
                    return None
                await asyncio.sleep(0.1)


async def _pipe(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def _first(*coroutines):
    """Run until either direction ends (or fails), then stop the other."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run(listen, chansrv):
    async def main():
        server = await Proxy(listen, chansrv).serve()
        async with server:
            await server.serve_forever()
    asyncio.run(main())
