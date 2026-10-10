from statistics import median, pstdev


MIN_PWM_CYCLES = 4
PERIOD_TOLERANCE_FRACTION = 0.03


def detect(edges):
    rising_times = [
        edge["time_s"]
        for edge in edges
        if edge["from_level"] == 0 and edge["to_level"] == 1
    ]
    falling_times = [
        edge["time_s"]
        for edge in edges
        if edge["from_level"] == 1 and edge["to_level"] == 0
    ]

    cycles = []
    fall_index = 0
    for cycle_index in range(len(rising_times) - 1):
        start = rising_times[cycle_index]
        next_start = rising_times[cycle_index + 1]
        while fall_index < len(falling_times) and falling_times[fall_index] <= start:
            fall_index += 1
        if fall_index >= len(falling_times):
            break
        falling = falling_times[fall_index]
        if falling < next_start:
            high_time = falling - start
            period = next_start - start
            if 0 < high_time < period:
                cycles.append((period, high_time))

    if len(cycles) < MIN_PWM_CYCLES:
        return {
            "protocol": "PWM",
            "score": 0.0,
            "evidence": {"complete_cycles": len(cycles)},
        }

    periods = [period for period, _ in cycles]
    high_times = [high_time for _, high_time in cycles]
    median_period = median(periods)
    median_high_time = median(high_times)
    tolerance = median_period * PERIOD_TOLERANCE_FRACTION
    consistent = [
        (period, high_time)
        for period, high_time in cycles
        if abs(period - median_period) <= tolerance
        and abs(high_time - median_high_time) <= tolerance
    ]

    consistency = len(consistent) / len(cycles)
    evidence_strength = min(1.0, len(consistent) / 8)
    score = consistency * evidence_strength
    period_cv = pstdev(periods) / median_period if median_period else 0.0

    return {
        "protocol": "PWM",
        "score": round(score, 4),
        "evidence": {
            "complete_cycles": len(cycles),
            "consistent_cycles": len(consistent),
            "period_seconds_median": median_period,
            "frequency_hz_estimate": 1.0 / median_period,
            "duty_cycle_fraction_median": median_high_time / median_period,
            "period_coefficient_of_variation": period_cv,
        },
    }