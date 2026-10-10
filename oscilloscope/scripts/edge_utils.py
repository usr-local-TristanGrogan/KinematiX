import bisect
import csv


REQUIRED_EDGE_COLUMNS = {
    "time_s",
    "from_level",
    "to_level",
    "edge",
}


def read_edges(path):
    with open(path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        missing = REQUIRED_EDGE_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "Edge CSV is missing required columns: "
                + ", ".join(sorted(missing))
            )

        edges = []
        for row in reader:
            edges.append({
                "time_s": float(row["time_s"]),
                "from_level": int(row["from_level"]),
                "to_level": int(row["to_level"]),
                "edge": row["edge"],
            })

    if any(
        edges[index]["time_s"] > edges[index + 1]["time_s"]
        for index in range(len(edges) - 1)
    ):
        raise ValueError("Edge timestamps must be sorted in ascending order.")
    return edges


def detect_uart(edges, baud_rate):
    bit_period = 1.0 / baud_rate
    times = [edge["time_s"] for edge in edges]
    levels = [edge["to_level"] for edge in edges]
    if not times:
        return {
            "protocol": f"UART {baud_rate}",
            "score": 0.0,
            "evidence": {"candidate_starts": 0, "valid_8n1_frames": 0},
        }

    capture_end = times[-1]
    start_edges = [
        edge["time_s"]
        for edge in edges
        if edge["from_level"] == 1 and edge["to_level"] == 0
        and edge["time_s"] + 9.5 * bit_period <= capture_end
    ]
    valid_frames = []
    next_allowed_start = float("-inf")

    def level_at(sample_time):
        edge_index = bisect.bisect_right(times, sample_time) - 1
        return levels[edge_index] if edge_index >= 0 else 1

    for start_time in start_edges:
        if start_time < next_allowed_start:
            continue
        if level_at(start_time + 0.5 * bit_period) != 0:
            continue

        value = 0
        for bit_index in range(8):
            bit = level_at(start_time + (1.5 + bit_index) * bit_period)
            value |= bit << bit_index

        if level_at(start_time + 9.5 * bit_period) == 1:
            valid_frames.append(value)
            next_allowed_start = start_time + 10 * bit_period

    score = min(1.0, len(valid_frames) / 3)

    return {
        "protocol": f"UART {baud_rate}",
        "score": round(score, 4),
        "evidence": {
            "candidate_starts": len(start_edges),
            "valid_8n1_frames": len(valid_frames),
            "bit_period_seconds": bit_period,
            "decoded_bytes_preview": valid_frames[:16],
        },
    }