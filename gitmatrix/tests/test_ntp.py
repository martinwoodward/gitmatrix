import asyncio
import struct

import pytest

from gitmatrix import _ntp


class FakeUDP:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False

    def setblocking(self, flag):
        assert flag is False

    def sendto(self, data, address):
        self.sent.append((bytes(data), address))

    def recv(self, n):
        if not self.replies or self.replies[0] is None:
            if self.replies:
                self.replies.pop(0)
            raise OSError(11)  # EAGAIN
        return self.replies.pop(0)

    def close(self):
        self.closed = True


def run(coro):
    # not asyncio.run(): that leaves no current loop for phew's setup in later tests
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def reply(seconds):
    return bytes(40) + struct.pack("!I", seconds) + bytes(4)


@pytest.fixture
def udp(monkeypatch):
    monkeypatch.setattr(_ntp, "_resolve", lambda host: ("10.0.0.1", 123))
    monkeypatch.setattr(_ntp, "POLL_MS", 1)
    set_to = []
    monkeypatch.setattr(_ntp, "set_rtc", set_to.append)

    def make(replies):
        sock = FakeUDP(replies)
        monkeypatch.setattr(_ntp, "_udp", lambda: sock)
        return sock, set_to

    return make


def test_sync_polls_without_blocking_and_sets_the_rtc(udp):
    sock, set_to = udp([None, None, reply(1_800_000_000 + _ntp.NTP_DELTA)])
    run(_ntp.sync())
    assert set_to == [1_800_000_000]
    assert sock.sent[0][0][0] == 0x1B and sock.sent[0][1] == ("10.0.0.1", 123)
    assert sock.closed


def test_no_answer_times_out_and_leaves_the_clock(udp, monkeypatch):
    monkeypatch.setattr(_ntp, "TIMEOUT_MS", 20)
    sock, set_to = udp([])
    with pytest.raises(OSError, match="timed out"):
        run(_ntp.sync())
    assert set_to == [] and sock.closed
