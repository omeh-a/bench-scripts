#!/bin/python3

import sys
import os

# FILTER_STR = "Result Summary:"
FILTER_STR = "Requested_Throughput,"
def fix_csv(file):
    real_csv_lines = []
    real_csv_found = False
    print(f"\nuncooking ipbench file {file}...\n")
    with open(file, mode="r") as f:
        for line in f:
            if not real_csv_found and line.startswith(FILTER_STR):
                real_csv_found = True
            if real_csv_found:
                real_csv_lines.append(line.strip())

    if len(real_csv_lines) == 0:
        print(f"\tno csv output? skipping...")
        exit(0)

    # overwrite file with just the lines that are useful
    out_file = file+".csv"
    with open(out_file, mode="w") as fo:
        for l in real_csv_lines:
            print(f"\t{l}")
            fo.write(f"{l}\n")
    print(f"\tdone! wrote to {out_file}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: path/to/ipbench_output")
        exit(-1)
    fix_csv(sys.argv[1])
