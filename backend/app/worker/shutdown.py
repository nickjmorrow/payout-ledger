"""The stop flag, in its own module so everything that reads it can import it without a cycle."""

import asyncio
import contextlib
import signal

# Set by SIGTERM or SIGINT. The loop stops claiming new work and exits once
# the task in hand is done; the sweeper covers a worker killed outright.
requested = asyncio.Event()


def install_signal_handlers() -> None:
    """Route SIGTERM and SIGINT into the shutdown event.

    Through the event loop, so the flag is set between awaits and the task in
    progress finishes instead of being killed mid-flight.
    """
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(signum, requested.set)
