"""SYNTHETIC attrition data for automated tests only.

These rows are randomly generated with a deliberately planted signal (more lateness/overtime -> more
likely to leave). They describe no real people, and any metrics computed on them say nothing about how
a model would perform on a real workforce.
"""

import numpy as np
import pandas as pd


def synthetic_dataset(n: int = 200, seed: int = 0, signal: bool = True) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    late = rng.uniform(0, 0.5, n)
    overtime = rng.uniform(0, 120, n)
    frame = pd.DataFrame({
        "tenure_months": rng.randint(1, 120, n),
        "late_rate_90d": late,
        "avg_overtime_minutes_90d": overtime,
        "recorded_absence_days_90d": rng.randint(0, 5, n),
        "attendance_days_recorded_90d": rng.randint(20, 65, n),
        "department": rng.choice(["1", "2", "3"], n),
    })
    # Some missing values, as real data would have.
    frame.loc[frame.sample(frac=0.1, random_state=seed).index, "late_rate_90d"] = np.nan
    if signal:
        frame["attrited"] = ((late * 4 + overtime / 60 + rng.normal(0, 0.5, n)) > 1.8).astype(int)
    else:
        frame["attrited"] = rng.randint(0, 2, n)  # pure noise
    return frame
