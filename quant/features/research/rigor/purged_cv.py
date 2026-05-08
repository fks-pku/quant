from typing import Any, Dict, List


def generate_purged_walkforward_splits(
    n_observations: int,
    train_window: int = 252,
    test_window: int = 63,
    step_days: int = 63,
    purge_days: int = 5,
    embargo_days: int = 21,
    min_train_observations: int = 126,
) -> List[Dict[str, Any]]:
    splits = []
    test_start = min_train_observations + embargo_days
    while test_start + test_window <= n_observations:
        train_end = test_start - embargo_days - 1
        train_start = max(0, train_end - train_window + 1)
        actual_train_size = train_end - train_start + 1
        if actual_train_size < min_train_observations:
            test_start += step_days
            continue
        if train_end >= test_start - purge_days:
            test_start += step_days
            continue
        splits.append({
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_start + test_window - 1,
            "train_size": actual_train_size,
            "test_size": test_window,
        })
        test_start += step_days
    return splits
