from __future__ import annotations

from typing import Iterable, List

import pandas_ta as ta  # noqa: F401


def append_symmetric_bbands(df, length: int, std: float) -> None:
    df.ta.bbands(length=length, lower_std=std, upper_std=std, append=True)


def resolve_indicator_column(df, exact_names: Iterable[str], prefix: str) -> str:
    for name in exact_names:
        if name in df.columns:
            return name

    matches = [column for column in df.columns if column.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        matches.sort()
        return matches[-1]

    raise KeyError(f"Indicator column not found for prefix '{prefix}'. Available columns: {list(df.columns)}")


def macd_name_candidates(prefix: str, fast: int, slow: int, signal: int) -> List[str]:
    candidates = [f"{prefix}_{fast}_{slow}_{signal}"]
    reordered = f"{prefix}_{min(fast, slow)}_{max(fast, slow)}_{signal}"
    if reordered not in candidates:
        candidates.append(reordered)
    return candidates
