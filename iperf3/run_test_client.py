#!/usr/bin/env python3
import os
import subprocess
import sys

def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-iperf3_cmd>", file=sys.stderr)
        return 1

    cmd_path = sys.argv[1]
    if not os.path.isfile(cmd_path):
        print(f"error: '{cmd_path}' not found", file=sys.stderr)
        return 1

    with open(cmd_path, "r") as fh:
        cmd_template = fh.read().strip()

    if not cmd_template:
        print(f"error: '{cmd_path}' is empty", file=sys.stderr)
        return 1

    if not os.environ.get("TEST_IP"):
        print("error: TEST_IP environment variable is not set", file=sys.stderr)
        return 1

    cmd_dir = os.path.dirname(os.path.abspath(cmd_path))
    orig_dir = os.getcwd()

    try:
        os.chdir(cmd_dir)
        for speed in range(100, 1001, 100):
            print(f"\n=== Running iperf3 at {speed} Mbit/s ===", flush=True)
            env = os.environ.copy()
            env["SPEED_MBIT"] = str(speed)
            try:
                result = subprocess.run(
                    ["bash", "-c", cmd_template],
                    env=env,
                    check=False,
                )
            except KeyboardInterrupt:
                print("\nInterrupted by user.", file=sys.stderr)
                return 130
            if result.returncode != 0:
                print(
                    f"warning: iperf3 exited with code {result.returncode} "
                    f"at {speed} Mbit/s; continuing.",
                    file=sys.stderr,
                )
    finally:
        os.chdir(orig_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
