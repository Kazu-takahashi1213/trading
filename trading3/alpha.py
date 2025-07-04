__all__ = ['ma_alpha', 'bbands', 'bb_alpha']

# Cell
import pandas as pd
import numpy as np


def ma_alpha(bars, fast=10, slow=100):
    close = bars["Close"]
    slow_ma = close.rolling(slow).mean()
    fast_ma = close.rolling(fast).mean()

    # Emit NaN while the signal is warming up
    signal = pd.Series(np.nan, index=close.index)
    signal[fast_ma >= slow_ma] = 1
    signal[fast_ma < slow_ma] = -1

    return signal


def bbands(close, window, stdev):
    ma = close.rolling(window).mean()
    std = close.rolling(window).std()
    return (ma + stdev * std, ma - stdev * std)


def bb_alpha(bars, length, stdev, mean_reverting):
    if mean_reverting:
        close_above_upper, close_below_lower = -1, 1
    else:
        close_above_upper, close_below_lower = 1, -1

    close = bars["Close"]

    bb_upper_band, bb_lower_band = bbands(close, length, stdev)

    signal = pd.Series(np.nan, index=close.index)
    signal[close > bb_upper_band] = close_above_upper
    signal[close < bb_lower_band] = close_below_lower
    # the signal is whatever was last triggered
    return signal.ffill()