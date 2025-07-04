__all__ = ['get_vertical_barriers', 'apply_pt_sl_on_t1', 'get_events', 'triple_barrier_method', 'fixed_horizon']

# Cell
import pandas as pd
from .multiprocess import mp_pandas_obj
from .utils import get_daily_vol


def get_vertical_barriers(close, t_events, num_days):
    t1 = close.index.searchsorted(t_events + pd.Timedelta(days=num_days))
    t1 = t1[t1 < close.shape[0]]
    t1 = pd.Series(close.index[t1], index=t_events[: t1.shape[0]])  # NaNs at the end
    return t1


def apply_pt_sl_on_t1(close, events, pt_sl, molecule):
    # apply stop loss/profit taking, if it takes place before t1 (end of event)
    events_ = events.loc[molecule]
    out = events_[["t1"]].copy(deep=True)

    if pt_sl[0] > 0:
        pt = pt_sl[0] * events_["trgt"]
    else:
        pt = pd.Series(index=events.index)  # NaNs

    if pt_sl[1] > 0:
        sl = -pt_sl[1] * events_["trgt"]
    else:
        sl = pd.Series(index=events.index)  # 'mo NaNs

    for loc, t1 in events_["t1"].fillna(close.index[-1]).iteritems():
        df0 = close[loc:t1]  # path prices
        df0 = (df0 / close[loc] - 1) * events_.at[loc, "side"]  # path returns
        out.loc[loc, "sl"] = df0[df0 < sl[loc]].index.min()  # earliest stop loss
        out.loc[loc, "pt"] = df0[df0 > pt[loc]].index.min()  # earliest profit take
    return out


def get_events(
    close, t_events, pt_sl, trgt, min_ret, num_threads=32, t1=False, side=None
):
    # 1) get target
    trgt = trgt.reindex(t_events)
    trgt = trgt[trgt > min_ret]
    # 2) get t1 (max holding period)
    if t1 is False:
        t1 = pd.Series(pd.NaT, index=t_events)
    # 3) form events object, apply stop loss on t1
    if side is None:
        side_, pt_sl_ = pd.Series(1.0, index=trgt.index), [pt_sl[0], pt_sl[0]]
    else:
        side_, pt_sl_ = side.loc[trgt.index], pt_sl[:2]
    events = pd.concat({"t1": t1, "trgt": trgt, "side": side_}, axis=1).dropna(
        subset=["trgt"]
    )
    df0 = mp_pandas_obj(
        func=apply_pt_sl_on_t1,
        pd_obj=("molecule", events.index),
        num_threads=num_threads,
        close=close,
        events=events,
        pt_sl=pt_sl_,
    )
    events["t1"] = df0.dropna(how="all").min(axis=1)  # pd.min ignores NaN
    if side is None:
        events = events.drop("side", axis=1)

    # store for later
    events["pt"] = pt_sl[0]
    events["sl"] = pt_sl[1]

    return events


def triple_barrier_method(bars, t_events, params, daily_vol, num_threads=32):
    target, pt, sl = params
    num_days = 100
    t1 = get_vertical_barriers(bars["Close"], t_events, num_days)

    events = get_events(
        bars["Close"],
        t_events=t_events,
        pt_sl=[pt, sl],
        t1=t1,
        num_threads=num_threads,
        trgt=daily_vol * target,
        min_ret=0.0,
    )

    assert not events.empty
    return events


def fixed_horizon(t_events, binarize_window):
    t1 = pd.Series(t_events, index=t_events).shift(-binarize_window)

    events = pd.DataFrame({"trgt": pd.Series(0, index=t1.index), "t1": t1})

    return events
