# GitMatrix firmware entry point. Everything lives in /lib/gitmatrix; see
# gitmatrix/_boot.py for the boot sequence. Ctrl-C drops to the REPL with the
# watchdog stopped, so mpremote can deploy.

from gitmatrix import _boot

_boot.run()
