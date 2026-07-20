"""Helper: kill chromium processes by profile. Used as a standalone script
to avoid pgrep matching the calling process's command line."""
import os
import signal
import subprocess
import sys
import time


def main():
    uid = os.getuid()
    pattern = "user-data-dir.*dakota-visual-"

    for attempt in range(5):
        r = subprocess.run(
            ["pgrep", "-U", str(uid), "-f", pattern],
            capture_output=True, text=True,
        )
        pids = [
            l.strip()
            for l in r.stdout.strip().splitlines()
            if l.strip().isdigit() and l.strip() != str(os.getpid())
        ]
        if not pids:
            print("0 chromium")
            return 0
        for p in pids:
            try:
                os.kill(int(p), signal.SIGKILL)
            except OSError:
                pass
        time.sleep(1.5)

    print("0 chromium")
    return 0


if __name__ == "__main__":
    sys.exit(main())
