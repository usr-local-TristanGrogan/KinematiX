import unittest

import PWM
import UART_9600
import UART_115200


def make_uart_edges(baud_rate, values):
    bit_period = 1.0 / baud_rate
    edges = []
    for frame_index, value in enumerate(values):
        frame_start = frame_index * 12 * bit_period
        frame_levels = [0]
        frame_levels.extend((value >> bit) & 1 for bit in range(8))
        frame_levels.append(1)
        current_level = 1

        for bit_index, next_level in enumerate(frame_levels):
            if next_level == current_level:
                continue
            edges.append({
                "time_s": frame_start + bit_index * bit_period,
                "from_level": current_level,
                "to_level": next_level,
                "edge": "rising" if next_level else "falling",
            })
            current_level = next_level
    edges.append({
        "time_s": len(values) * 12 * bit_period,
        "from_level": 1,
        "to_level": 0,
        "edge": "falling",
    })
    return edges


def make_pwm_edges(cycle_count=20):
    period = 0.002
    high_time = 0.0005
    edges = []
    for cycle_index in range(cycle_count):
        start = cycle_index * period
        edges.append({
            "time_s": start,
            "from_level": 0,
            "to_level": 1,
            "edge": "rising",
        })
        edges.append({
            "time_s": start + high_time,
            "from_level": 1,
            "to_level": 0,
            "edge": "falling",
        })
    return sorted(edges, key=lambda edge: edge["time_s"])


class ProtocolDetectorTests(unittest.TestCase):
    def test_detects_uart_9600_frames(self):
        result = UART_9600.detect(make_uart_edges(9600, [0x55, 0xA3, 0x00]))
        self.assertEqual(result["evidence"]["valid_8n1_frames"], 3)
        self.assertGreater(result["score"], 0.8)

    def test_detects_uart_115200_frames(self):
        result = UART_115200.detect(
            make_uart_edges(115200, [0x55, 0xA3, 0x00])
        )
        self.assertEqual(result["evidence"]["valid_8n1_frames"], 3)
        self.assertGreater(result["score"], 0.8)

    def test_detects_stable_pwm(self):
        result = PWM.detect(make_pwm_edges())
        self.assertGreater(result["score"], 0.8)
        self.assertAlmostEqual(
            result["evidence"]["duty_cycle_fraction_median"], 0.25
        )


if __name__ == "__main__":
    unittest.main()