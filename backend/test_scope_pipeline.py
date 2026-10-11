import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.scope_pipeline import run_capture_pipeline


class ScopePipelineTests(unittest.TestCase):
    def test_capture_edges_are_analyzed_and_report_is_saved_per_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)

            def fake_capture(output_dir, resource_index, extra_metadata):
                output_dir.mkdir(parents=True)
                edges_path = output_dir / "edges.csv"
                edges_path.write_text("time_s,from_level,to_level,edge\n", encoding="utf-8")
                return {
                    "output_dir": str(output_dir),
                    "edges_csv": str(edges_path),
                    "sample_count": 24,
                    "edge_count": 0,
                    "metadata": extra_metadata,
                }

            analyzed_files = []

            def fake_analyze(edge_file):
                analyzed_files.append(Path(edge_file))
                return {"classification": "unknown", "edge_count": 0}

            with patch("oscilloscope.capture.capture_once", side_effect=fake_capture):
                with patch("oscilloscope.scripts.detect_protocol.analyze", side_effect=fake_analyze):
                    result = run_capture_pipeline(
                        "job_001",
                        "D13",
                        resource_index=0,
                        output_root=output_root,
                    )

            expected_edges = output_root / "job_001" / "D13" / "edges.csv"
            self.assertEqual(analyzed_files, [expected_edges])
            self.assertEqual(result["protocol_detection"]["classification"], "unknown")
            saved_report = json.loads(Path(result["protocol_detection_json"]).read_text(encoding="utf-8"))
            self.assertEqual(saved_report["classification"], "unknown")

    def test_path_traversal_is_rejected_before_capture(self):
        with patch("oscilloscope.capture.capture_once") as capture_once:
            with self.assertRaises(ValueError):
                run_capture_pipeline("../job", "D13")
        capture_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()