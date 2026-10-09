"""GPU stock per data center: `runpodctl gpu list --include-unavailable | python3 scripts/gpu_stock.py`."""

import json
import sys

# Default: the network volume's region.
REGION = sys.argv[1] if len(sys.argv) > 1 else "EU-RO-1"
data = json.load(sys.stdin)
gpus = data if isinstance(data, list) else data.get("gpus", [])
for gpu in sorted(gpus, key=lambda g: g.get("securePricePerHr") or 99):
    if gpu.get("memoryInGb", 0) < 24:
        continue
    availability = gpu.get("dataCenterAvailability", [])
    here = next(
        (a["stockStatus"] for a in availability if a["dataCenterId"] == REGION), "-"
    )
    print(
        f"{gpu['displayName']:<22} {gpu['memoryInGb']:>3} GB  "
        f"${gpu.get('securePricePerHr')}/h  {REGION}: {here}"
    )
