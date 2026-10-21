from __future__ import annotations

import argparse
import subprocess
import time


def tunnel_present(output: str, port: int) -> bool:
    endpoint = f"tcp:{port}"
    return any(len(parts := line.split()) >= 2 and parts[-2:] == [endpoint, endpoint] for line in output.splitlines())


def maintain(serial: str, port: int) -> None:
    last_state = None
    while True:
        try:
            state = subprocess.run(["adb", "-s", serial, "get-state"], capture_output=True, text=True, timeout=8)
            if state.returncode or state.stdout.strip() != "device":
                current = "waiting for authorized Android device"
            else:
                listing = subprocess.run(["adb", "-s", serial, "reverse", "--list"], capture_output=True, text=True, timeout=8)
                if listing.returncode or not tunnel_present(listing.stdout, port):
                    result = subprocess.run(["adb", "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"], capture_output=True, text=True, timeout=8)
                    current = "USB tunnel configured" if result.returncode == 0 else "USB tunnel unavailable"
                else:
                    current = "USB tunnel configured"
        except (OSError, subprocess.TimeoutExpired):
            current = "ADB unavailable"
        if current != last_state:
            print(current, flush=True)
            last_state = current
        time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain the authorized phone's ADB reverse tunnel; no SMS or settings writes.")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18098)
    args = parser.parse_args()
    if not args.serial.strip() or not 1 <= args.port <= 65535:
        parser.error("Device serial and port must be valid.")
    try:
        maintain(args.serial, args.port)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())