"""单机可立即提交的 GPU 数。stdin 为 `scontrol -o show node`。

集群 2 以及 MI210 / Z100：--cores 8
集群 1 的 gpuA800：--cores 9
"""

import argparse
import re
import sys


def grab(line, key):
    match = re.search(r"(?:^|\s)%s=(\S+)" % re.escape(key), line)
    return match.group(1) if match else ""


def ngpu(tres):
    if not tres or tres == "(null)":
        return 0
    match = re.search(r"(?:^|,)gres/(?:gpu|dcu)=(\d+)", tres)
    if match:
        return int(match.group(1))
    return sum(int(item) for item in re.findall(r"gres/(?:gpu|dcu):[^=,]+=(\d+)", tres))


def main():
    parser = argparse.ArgumentParser(description="按单机空闲 GPU 和 CPU 计算可立即提交的卡数")
    parser.add_argument("--cores", type=int, default=8, help="每张卡要申请的 CPU 核数")
    args = parser.parse_args()
    rows = []
    for line in sys.stdin:
        if "NodeName=" not in line:
            continue
        state = grab(line, "State").upper()
        if any(key in state for key in ("DOWN", "DRAIN", "FAIL", "UNKNOWN")):
            continue
        part = grab(line, "Partitions").split(",")[0].rstrip("*")
        if not re.search(r"gpu|dcu", part, re.I):
            continue
        if re.match(r"(emic|gznet|ex\d|telecom)", part, re.I):
            continue
        cpu_free = max(int(grab(line, "CPUTot") or 0) - int(grab(line, "CPUAlloc") or 0), 0)
        gpu_free = max(ngpu(grab(line, "CfgTRES")) - ngpu(grab(line, "AllocTRES")), 0)
        if not gpu_free:
            continue
        cards = min(gpu_free, cpu_free // args.cores)
        rows.append((cards, grab(line, "NodeName"), part, gpu_free, cpu_free))
    rows.sort(key=lambda row: (-row[0], row[1]))
    largest = max([row[0] for row in rows] or [0])
    print("单机可立即提交（1卡%d核）最大 %d 卡" % (args.cores, largest))
    for cards, name, part, gpu_free, cpu_free in rows:
        flag = "可交 %d 卡" % cards if cards else "核不够，交不上"
        print("%s %s 空卡 %d 空核 %d %s" % (name, part, gpu_free, cpu_free, flag))


if __name__ == "__main__":
    main()
