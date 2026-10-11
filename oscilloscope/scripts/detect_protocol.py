import argparse
import json
from pathlib import Path

if __package__:
    from . import PWM, UART_9600, UART_115200
    from .edge_utils import read_edges
else:
    import PWM
    import UART_9600
    import UART_115200
    from edge_utils import read_edges


MINIMUM_SCORE = 0.5
AMBIGUITY_MARGIN = 0.10


def latest_edge_file(captures_dir):
    capture_dirs = sorted(
        (path for path in captures_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    for capture_dir in capture_dirs:
        edge_file = capture_dir / "edges.csv"
        if edge_file.is_file():
            return edge_file
    raise FileNotFoundError(f"No capture edges.csv found under {captures_dir}")


def analyze(edge_file):
    edges = read_edges(edge_file)
    candidates = [
        UART_9600.detect(edges),
        UART_115200.detect(edges),
        PWM.detect(edges),
    ]
    candidates.sort(key=lambda result: result["score"], reverse=True)

    best_score = candidates[0]["score"] if candidates else 0.0
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
    if best_score < MINIMUM_SCORE:
        classification = "unknown"
    elif best_score - second_score <= AMBIGUITY_MARGIN:
        classification = "ambiguous"
    else:
        classification = candidates[0]["protocol"]

    return {
        "input_file": str(edge_file),
        "edge_count": len(edges),
        "classification": classification,
        "score_note": "Heuristic evidence scores, not calibrated probabilities.",
        "candidates": candidates,
    }


def main():
    project_dir = Path(__file__).resolve().parents[1]
    captures_dir = project_dir / "captures"

    parser = argparse.ArgumentParser(
        description="Compare an edge CSV against UART and PWM detectors."
    )
    parser.add_argument(
        "edges_csv",
        nargs="?",
        type=Path,
        help="Edge CSV path; defaults to the newest capture's edges.csv.",
    )
    args = parser.parse_args()
    edge_file = args.edges_csv or latest_edge_file(captures_dir)
    result = analyze(edge_file)

    output_file = edge_file.parent / "protocol_detection.json"
    with output_file.open("w", encoding="utf-8") as file:
        json.dump(result, file, indent=2)

    print(f"Input: {edge_file}")
    print(f"Edges analyzed: {result['edge_count']}")
    print(f"Result: {result['classification']}")
    for candidate in result["candidates"]:
        print(f"  {candidate['protocol']}: score {candidate['score']:.2f}")
        for key, value in candidate["evidence"].items():
            print(f"    {key}: {value}")
    print(f"Saved report: {output_file}")


if __name__ == "__main__":
    main()