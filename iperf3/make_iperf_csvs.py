#!/usr/bin/env python3
import csv
import json
import os
import sys

def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-iperf3_cmd>", file=sys.stderr)
        return 1

    cmd_path = sys.argv[1]
    if not os.path.isfile(cmd_path):
        print(f"error: '{cmd_path}' not found", file=sys.stderr)
        return 1

    print(f"Trying to make csv for {cmd_path}...")
    cmd_dir = os.path.dirname(os.path.abspath(cmd_path))
    cmd_basename = os.path.basename(cmd_path)

    json_files = sorted(
        f for f in os.listdir(cmd_dir)
        if f.endswith(".json") and f != cmd_basename
        and os.path.isfile(os.path.join(cmd_dir, f))
    )

    if not json_files:
        print(f"error: no .json files found in {cmd_dir}", file=sys.stderr)
        return 1

    sum_metrics = [
        ("sum_bps_mbit",        "bits_per_second", True),
        ("sum_lost_packets",    "lost_packets",    False),
        ("sum_packets",         "packets",         False),
        ("sum_jitter_ms",       "jitter_ms",       False),
    ]

    reverse_metrics = [
        ("reverse_sum_bps_mbit",     "bits_per_second", True),
        ("reverse_sum_lost_packets", "lost_packets",    False),
        ("reverse_sum_packets",      "packets",         False),
        ("reverse_sum_jitter_ms",    "jitter_ms",       False),
    ]

    cpu_metrics = [
        ("remote_cpu_total",  "remote_total"),
        ("remote_cpu_user",   "remote_user"),
        ("remote_cpu_system", "remote_system"),
    ]

    data = {}
    has_bidir = False

    for jf in json_files:
        fpath = os.path.join(cmd_dir, jf)
        try:
            with open(fpath, "r") as fh:
                j = json.load(fh)
        except (json.JSONDecodeError, IOError) as e:
            print(f"warning: could not parse {jf}: {e}", file=sys.stderr)
            continue

        start = j.get("start", {})
        target_bitrate = start.get("target_bitrate")
        if target_bitrate is None:
            target_bitrate = start.get("test_start", {}).get("target_bitrate")
        if target_bitrate is None:
            print(f"warning: no target_bitrate in {jf}, skipping", file=sys.stderr)
            continue

        target_mbit = round(target_bitrate / 1e6)
        end = j.get("end", {})
        row = {}

        sum_data = end.get("sum", {})
        for label, key, is_bps in sum_metrics:
            val = sum_data.get(key)
            if val is not None and is_bps:
                val = round(val / 1e6, 2)
            row[label] = val

        reverse_sum = end.get("sum_bidir_reverse")
        if reverse_sum:
            has_bidir = True
            for label, key, is_bps in reverse_metrics:
                val = reverse_sum.get(key)
                if val is not None and is_bps:
                    val = round(val / 1e6, 2)
                row[label] = val

        cpu = end.get("cpu_utilization_percent", {})
        for label, key in cpu_metrics:
            row[label] = cpu.get(key)

        data[target_mbit] = row

    if not data:
        print("error: no valid data extracted", file=sys.stderr)
        return 1

    targets = sorted(data.keys())

    row_labels = [m[0] for m in sum_metrics]
    if has_bidir:
        row_labels += [m[0] for m in reverse_metrics]
    row_labels += [m[0] for m in cpu_metrics]

    out_path = os.path.join(cmd_dir, "iperf3_sweep_results.csv")
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["metric"] + [f"{t}M" for t in targets])
        for label in row_labels:
            writer.writerow([label] + [data[t].get(label, "") for t in targets])

    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
