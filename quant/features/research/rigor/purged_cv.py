from typing import Any, Iterable, List

import pandas as pd


def generate_purged_walk_forward_splits(
    dates: Iterable[Any],
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    purge_days: int,
    embargo_days: int,
    min_train_observations: int,
) -> List[dict]:
    ordered = sorted(pd.Timestamp(value) for value in dates)
    if not ordered:
        return []

    train_window = max(1, int(train_window_days))
    test_window = max(1, int(test_window_days))
    step = max(1, int(step_days))
    purge = max(0, int(purge_days))
    embargo = max(0, int(embargo_days))
    min_train = max(1, int(min_train_observations))

    splits = []
    train_start_idx = 0
    while True:
        train_end_idx = train_start_idx + train_window - 1
        if train_end_idx >= len(ordered):
            break

        if train_end_idx - train_start_idx + 1 < min_train:
            break

        test_start_idx = train_end_idx + max(1, purge)
        test_end_idx = test_start_idx + test_window - 1
        if test_end_idx >= len(ordered):
            break

        train_start = ordered[train_start_idx]
        train_end = ordered[train_end_idx]
        test_start = ordered[test_start_idx]
        test_end = ordered[test_end_idx]
        if train_end < test_start and (test_start - train_end).days >= purge:
            splits.append(
                {
                    "train_start": train_start.date().isoformat(),
                    "train_end": train_end.date().isoformat(),
                    "test_start": test_start.date().isoformat(),
                    "test_end": test_end.date().isoformat(),
                    "train_start_index": train_start_idx,
                    "train_end_index": train_end_idx,
                    "test_start_index": test_start_idx,
                    "test_end_index": test_end_idx,
                    "purge_days": purge,
                    "embargo_days": embargo,
                }
            )

        train_start_idx += step + embargo

    return splits
