# Sets the RTC from NTP once WiFi is up, then every few hours. ntptime blocks
# for up to its socket timeout, so this sends the query on a non-blocking UDP
# socket and polls for the answer from asyncio: a slow server never stalls the
# display or the speaker. The lookup goes through fetch's DNS cache.

import socket
import struct
import time

try:
    import machine
except ImportError:
    machine = None

from gitmatrix import _compat, _log

HOST = "pool.ntp.org"
PORT = 123
TIMEOUT_MS = 3000
POLL_MS = 50
RESYNC_MS = 6 * 3600 * 1000
RETRY_MS = 60 * 1000

# NTP counts from 1900; MicroPython's epoch is 1970 or (older ports) 2000
NTP_DELTA = 2208988800 if time.gmtime(0)[0] == 1970 else 3155673600

synced = False


async def _sleep_ms(ms):
    import asyncio

    if hasattr(asyncio, "sleep_ms"):
        await asyncio.sleep_ms(ms)
    else:
        await asyncio.sleep(ms / 1000)


def set_rtc(t):
    tm = time.gmtime(t)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))


def _resolve(host):
    import fetch

    return fetch.resolve(host, PORT)[-1]


def _udp():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


async def query(host=HOST):
    """Seconds since the epoch from an NTP server, without blocking the loop."""
    address = _resolve(host)
    s = _udp()
    try:
        s.setblocking(False)
        packet = bytearray(48)
        packet[0] = 0x1B  # LI 0, version 3, client
        s.sendto(packet, address)
        start = _compat.ticks_ms()
        reply = None
        while reply is None and _compat.ticks_diff(_compat.ticks_ms(), start) <= TIMEOUT_MS:
            try:
                reply = s.recv(48)
            except OSError:  # EAGAIN: nothing yet
                await _sleep_ms(POLL_MS)
    finally:
        s.close()
    if reply is None:
        raise OSError("NTP timed out")
    if len(reply) < 48:
        raise OSError("short NTP reply")
    seconds = struct.unpack("!I", reply[40:44])[0]
    if seconds == 0:
        raise OSError("NTP server not synchronised")
    return seconds - NTP_DELTA


async def sync(host=HOST):
    global synced
    set_rtc(await query(host))
    synced = True


async def run(connected, tick_ms=5000):
    """Keep the clock set while `connected()` is true."""
    due = None
    while True:
        await _sleep_ms(tick_ms)
        now = _compat.ticks_ms()
        if not connected() or (due is not None and _compat.ticks_diff(now, due) < 0):
            continue
        try:
            await sync()
            due = _compat.ticks_add(now, RESYNC_MS)
        except Exception as e:  # noqa: BLE001 - try again later; the clock shows --:--
            _log.log(f"ntp: {_compat.error_text(e)}")
            due = _compat.ticks_add(now, RETRY_MS)
