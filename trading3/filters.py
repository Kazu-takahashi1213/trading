__all__ = ['cusum']

# Cell
import pandas as pd


def cusum(g_raw, h):
    """
    The CUSUM filter is a quality-control method, designed to detect a shift in the mean value of
    a measured quantity away from a target value.
    """
    t_events, s_pos, s_neg = [], 0, 0
    diff = g_raw.diff()
    for i in diff.index[1:]:
        s_pos, s_neg = max(0, s_pos + diff.loc[i]), min(0, s_neg + diff.loc[i])
        if s_neg < -h:
            s_neg = 0
            t_events.append(i)
        elif s_neg > h:
            s_pos = 0
            t_events.append(i)
    return pd.DatetimeIndex(t_events)