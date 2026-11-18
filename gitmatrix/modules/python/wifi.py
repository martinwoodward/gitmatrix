# Art Matrix's wifi module API, for plugins written against it. GitMatrix's
# runtime owns the radio (gitmatrix/_wifi.py), so these only report on it;
# connect() never blocks or reconfigures the network.

from gitmatrix import _runtime


def _mgr():
    return _runtime.wifi


def _sta():
    m = _mgr()
    return m.sta if m is not None else None


def tick():
    return is_connected()


def connect(ssid=None, psk=None, hostname=None, timeout=60, retries=5):  # noqa: ARG001 - Art Matrix signature
    return is_connected()


def disconnect():
    pass


def status():
    m = _mgr()
    if m is None or m.sta is None:
        return 0, "Idle"
    try:
        code = m.sta.status()
    except (OSError, AttributeError):
        code = 0
    return code, m.sta_status()


def is_connected():
    m = _mgr()
    return m is not None and m.connected()


def ipv4():
    m = _mgr()
    return m.ip if m is not None and m.connected() else None


def ipv6():
    sta = _sta()
    try:
        return sta.ipconfig("addr6")[0][0] if sta is not None else None
    except (OSError, AttributeError, IndexError, ValueError):
        return None


def ip():
    return ipv4()


def _ifconfig(i):
    sta = _sta()
    try:
        return sta.ifconfig()[i] if sta is not None and is_connected() else None
    except (OSError, AttributeError, IndexError):
        return None


def subnet():
    return _ifconfig(1)


def gateway():
    return _ifconfig(2)


def nameserver():
    return _ifconfig(3)
