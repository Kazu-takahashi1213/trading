__all__ = ['get_bins', 'drop_labels']

# Cell

import logging
import pandas as pd
import numpy as np


def get_bins(events, close):
    """
    Compute event's outcome (including side information, if provided).
    events is a DataFrame where:
    -events.index is event's starttime
    -events['t1'] is event's endtime
    -events['trgt'] is event's target
    -events['side'] (optional) implies the algo's position side
    Case 1: ('side' not in events): bin in (-1, 1) <- label by price action
    Case 2: ('side' in events): bin in (0, 1) <- label by pnl (meta-labeling)
    """
    # 1) prices aligned with events
    events_ = events.dropna(subset=["t1"])
    px = events_.index.union(events_["t1"].values).drop_duplicates()
    px = close.reindex(px, method="bfill")
    # 2) create out object
    out = pd.DataFrame(index=events_.index)

    out["ret"] = px.loc[events_["t1"].values].values / px.loc[events_.index] - 1
    if "side" in events_:
        out["ret"] *= events_["side"]  # meta-labeling

    out["trgt"] = events_["trgt"]
    out["bin"] = np.sign(out["ret"].fillna(0))

    if "side" in events_:
        out.loc[out["ret"] <= 0, "bin"] = 0
        out["side"] = events_["side"]

    return out


def drop_labels(events, mit_pct=0.2):
    # apply weights, drop labels with insufficient examples
    while True:
        df0 = events["bin"].value_counts(normalize=True)
        if df0.min() > mit_pct or df0.shape[0] < 3:
            break
        logging.info(f"Dropped label {df0.idxmin()} {df0.min()}")
        events = events[events["bin"] != df0.idxmin()]
    return events