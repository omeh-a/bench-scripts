#!/usr/bin/env python3
"""
Run ipbench latency tests against a Linux target and write the results as CSV.

Warning: I vibe-coded a bunch of this file...

Example:
    linux_benchmark.py -l results.csv 10.0.0.2 --tcp \\
        --throughputs 100M 200M 500M 1G --clients vb01 vb02 vb03 vb04 \\
        --autobench_run_echo experiments/tcp/nthread/echoitn --echo_threads 4
"""

import argparse
import csv
import datetime as dt
import re
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path, PurePosixPath

IPBENCHD_PORT = 8036
TARGET_TEST_PORT = 8037
ECHO_PORTS = {"tcp": 1237, "udp": 1235}
DEFAULT_PACKET_SIZES = {"tcp": 1460, "udp": 1472}

# autobench's buildroot package installs its whole tree (with built binaries) here.
AUTOBENCH_DIR = PurePosixPath("/usr/share/ts_autobench")

LATENCY_FIELDS = [
    "Requested_Throughput",
    "Receive_Throughput",
    "Send_Throughput",
    "Packet_Size",
    "Minimum_RTT",
    "Average_RTT",
    "Maximum_RTT",
    "Stdev_RTT",
    "Median_RTT",
]

SI_MULTIPLIERS = {"": 1, "K": 10**3, "M": 10**6, "G": 10**9}


def parse_throughput(text: str) -> int:
    """Bits/s, with an optional decimal K, M or G suffix."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMG]?)", text)
    if not match:
        raise argparse.ArgumentTypeError(f"invalid throughput {text!r}")
    bits = Decimal(match[1]) * SI_MULTIPLIERS[match[2]]
    if bits <= 0 or bits != int(bits):
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive whole number of bits")
    return int(bits)


def autobench_binary(text: str) -> str:
    # exec_path ends up in `sh -c "exec <path> ..."` on the target, and inside the
    # comma/equals-delimited --target-test-args string, so allow only characters
    # that are inert in both, and keep it inside AUTOBENCH_DIR.
    rel = PurePosixPath(text)
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", text) or rel.is_absolute() or ".." in rel.parts:
        raise argparse.ArgumentTypeError(f"invalid path {text!r} (relative to {AUTOBENCH_DIR})")
    return str(AUTOBENCH_DIR / rel)


def abort_clients(clients: list[str]):
    for client in clients:
        with socket.create_connection((client, IPBENCHD_PORT), timeout=10) as sock:
            sock.sendall(b"ABORT\n")
            sock.shutdown(socket.SHUT_WR)
            while sock.recv(4096):
                pass


def make_arg_list(**opts) -> str:
    return ",".join(f"{k}={v}" for k, v in opts.items())


def run_benchmark(args, throughput: int, packet_size: int, debug_log) -> str:
    echo_port = ECHO_PORTS[args.protocol]

    argv = ["ipbench"] + (["--debug"] if args.debug else [])

    argv += [
        "--test",
        "latency",
        "--test-args",
        make_arg_list(
            socktype=args.protocol,
            bps=throughput // len(args.clients),
            size=packet_size,
            warmup=args.warmup_seconds,
            cooldown=args.cooldown_seconds,
            samples=args.samples // len(args.clients),
        ),
        "--port",
        str(IPBENCHD_PORT),
    ]
    for client in args.clients:
        argv += ["--client", client]

    target_args = [make_arg_list(warmup=args.warmup_seconds, cooldown=args.cooldown_seconds, cpus=4)]
    if args.autobench_run_echo:
        # We launch the echo server ourselves, so we pick its port and point
        # the clients at the same one.
        target_test = "cputarget_run_echo"
        target_args.append(
            make_arg_list(
                num_threads=args.echo_threads,
                start_port=echo_port,
                exec_path=args.autobench_run_echo,
            )
        )
    else:
        target_test = args.targettest

    argv += [
        "--test-target",
        args.ip,
        "--test-port",
        str(echo_port),
        "--target-test",
        target_test,
        "--target-test-hostname",
        args.ip,
        "--target-test-port",
        str(TARGET_TEST_PORT),
        "--target-test-args",
        ",".join(target_args + args.targettest_extra_args),
    ]

    if debug_log:
        debug_log.write(f"\n### {' '.join(argv)}\n")
        debug_log.flush()
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=debug_log, text=True,
                       timeout=args.run_timeout)
    if debug_log:
        debug_log.write(f"### stdout:\n{p.stdout}")
        debug_log.flush()
    p.check_returncode()
    return p.stdout

# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# CSV things to deal with ipbench not doing this on its own #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

def parse_ipbench_output(stdout: str) -> list[str]:
    """Return one CSV row: the latency fields, overall CPU %, then per-CPU %s.

    Expects a latency line followed by either "<avg>" (cpu_target_lukem) or
    "<avg>,CPU0: <x>,CPU1: <y>,..." (cputarget_run_echo). ipbench exits 0 even
    when a test fails, so anything else is treated as a failed run.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 2:
        sys.exit(f"error: unexpected ipbench output: {lines!r}")

    latency = lines[0].split(",")
    cpu_overall, *per_cpu = lines[1].split(",")
    per_cpu = [re.fullmatch(r"CPU\d+: (\S+)", f.strip()) for f in per_cpu]
    if len(latency) != len(LATENCY_FIELDS) or not all(per_cpu):
        sys.exit(f"error: unexpected ipbench output: {lines!r}")

    row = latency + [cpu_overall] + [m[1] for m in per_cpu]
    try:
        [float(v) for v in row]
    except ValueError:
        sys.exit(f"error: non-numeric ipbench output: {lines!r}")
    return row

# # # # # # # # # #
# argparser stuff #
# # # # # # # # # #

def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ip", help="IP address of the machine to benchmark")
    parser.add_argument(
        "-l",
        "--logfile",
        type=Path,
        default=Path(dt.datetime.now().strftime("output-%Y-%m-%dT%H-%M-%S.csv")),
        help="CSV file to write (must not already exist)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="run ipbench with --debug and save its stderr to <logfile>.debug.log",
    )
    parser.add_argument("--clients", nargs="+", default=["vb04", "vb05", "vb06", "vb07"])
    parser.add_argument("--udp", action="store_const", dest="protocol", const="udp", default="udp")
    parser.add_argument("--tcp", action="store_const", dest="protocol", const="tcp")
    parser.add_argument("--warmup-seconds", type=int, default=10)
    parser.add_argument("--cooldown-seconds", type=int, default=10)
    parser.add_argument("--samples", type=int, default=200_000)
    parser.add_argument("--packet-sizes", nargs="+", type=int)
    parser.add_argument(
        "--throughputs",
        nargs="+",
        type=parse_throughput,
        required=True,
        help="total throughputs in bits/s; accepts K, M, G suffixes (e.g. 100M 1G)",
    )
    parser.add_argument("--run_timeout", type=int, default=1800,
                        help="seconds before a single ipbench run is abandoned")

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--targettest")
    target.add_argument(
        "--autobench_run_echo",
        type=autobench_binary,
        metavar="REL_PATH",
        help=f"launch this echo server (relative to {AUTOBENCH_DIR}) with cputarget_run_echo",
    )
    parser.add_argument("--echo_threads", type=int, default=1,
                        help="threads for the --autobench_run_echo server")
    parser.add_argument("--targettest_extra_args", nargs="*", default=[])

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    packet_sizes = args.packet_sizes or [DEFAULT_PACKET_SIZES[args.protocol]]

    # "x" so an existing results file is never silently overwritten.
    debug_log = open(f"{args.logfile}.debug.log", "x") if args.debug else None
    with open(args.logfile, "x", newline="") as out:
        writer = csv.writer(out, lineterminator="\n")
        n_cpus = None

        abort_clients(args.clients)
        try:
            for throughput in args.throughputs:
                for packet_size in packet_sizes:
                    print(f"{throughput} bps, {packet_size} B packets", file=sys.stderr)
                    row = parse_ipbench_output(run_benchmark(args, throughput, packet_size, debug_log))

                    # Header is deferred until we know how many CPUs the target reports.
                    if n_cpus is None:
                        n_cpus = len(row) - len(LATENCY_FIELDS) - 1
                        writer.writerow(LATENCY_FIELDS + ["CPU_Utilisation"]
                                        + [f"CPU{i}_Utilisation" for i in range(n_cpus)])
                    writer.writerow(row)
                    # Flush per row so a crash or Ctrl-C mid-sweep keeps completed results.
                    out.flush()
                    print(",".join(row))
        finally:
            abort_clients(args.clients)
