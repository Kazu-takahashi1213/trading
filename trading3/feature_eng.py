__all__ = ['roll_measure', 'roll_impact', 'kyle', 'amihud', 'autocorr', 'stdev', 'log', 'ffd', 'volratio', 'get_bars',
           'engineer_feature', 'compute_feature', 'define_features', 'define_feature_configs', 'SYMBOLS_CSV',
           'SYMBOLS_DICT', 'FEATURES']

# Cell
from mlfinlab.microstructural_features import (
    get_roll_measure,
    get_roll_impact,
    get_bar_based_kyle_lambda,
    get_bar_based_amihud_lambda,
)
import pandas as pd
import numpy as np
import logging
from .load_data import load_feat, save_feat
from .frac_diff import frac_diff_ffd
from .load_data import get_data, SYMBOLS_CSV

SYMBOLS_CSV = SYMBOLS_CSV.copy()
SYMBOLS_CSV.columns = SYMBOLS_CSV.columns.str.lower()
SYMBOLS_DICT = SYMBOLS_CSV.T.to_dict()


def roll_measure(df, window=20):
    """The Roll measure attempts to estimate the bid-ask spread (i.e. liquidity) of an instrument"""
    return get_roll_measure(df["Close"], window)


def roll_impact(df, window=20):
    """The Roll measure divided by dollar volume"""
    return roll_measure(df, window) / df["Dollar Volume"] * 1e9


def kyle(df, window=20):
    """A measure of market impact cost (i.e. liquidity) from Kyle (1985)"""
    return get_bar_based_kyle_lambda(df["Close"], df["Volume"], window) * 1e9


def amihud(df, window=20):
    """A measure of market impact cost (i.e. liquidity) from Amihud (2002)"""
    return get_bar_based_amihud_lambda(df["Close"], df["Dollar Volume"], window) * 1e9


def autocorr(df, window, lag):
    """The raw price series' serial correlation"""
    return df["Close"].rolling(window).apply(lambda x: x.autocorr(lag=lag), raw=False)


def stdev(df, window):
    """The raw price series' standard deviation"""
    return df["Close"].rolling(window).std()


def log(df):
    """First difference of log-transformed prices"""
    return np.log(df["Close"]).diff()


def ffd(df, d):
    """Fractionally differentiated prices"""
    return frac_diff_ffd(np.log(df[["Close"]]), d)["Close"]


def volratio(df, com):
    """
    EWM of bar-by-bar buy volume divided by total volume
    (i.e. a value >0.50 would indicate buyers driving the market)
    """
    buy_vol, vol = df["Buy Volume"], df["Volume"]
    return (buy_vol / vol).ewm(com=com).mean()


FEATURES = {
    "auto": autocorr,
    "stdev": stdev,
    "roll": roll_measure,
    "rollimp": roll_impact,
    "kyle": kyle,
    "amihud": amihud,
    "volratio": volratio,
    "log": log,
    "ffd": ffd,
    "close": lambda df: df["Close"],
    "sector": lambda df: df["Close"],
}


# def engineer_features(bars, features):
# """Parse and compute features"""
# df = bars.copy(deep=True)
# parse_num = lambda x: float(x) if "." in x else int(x)

# for feature in features:
#     logging.debug(feature)
#     name, *params = feature.split("_")
#     params = map(parse_num, params)
#     df[feature] = FEATURES[name](df, *params)

# return df.drop(columns=bars.columns)

def get_bars(deck, symbol, config):
    if symbol in deck:
        # TODO: Remove deep copy
        bars = deck[symbol]['bars'].copy(deep=True)
    else:
        # We're loading a feature external to the price data of our trading universe
        bars = get_data(symbol, "minutely", config["start_date"], config["end_date"])

    return bars


def engineer_feature(deck, for_symbol, config, feat_conf):
    """Parse and compute a feature"""
    symbol = feat_conf['symbol'] = feat_conf.get('symbol', for_symbol)
    feat = load_feat(config, feat_conf)
    if feat is not None:
        return feat

    df = get_bars(deck, symbol, config)

    logging.debug(f"Computing {feat_conf['name']} for {for_symbol}: {feat_conf}")

    if isinstance(symbol, dict):
        # We're computing a feature on a feature
        df = engineer_feature(deck, for_symbol, config, symbol)

    feat = compute_feature(deck, for_symbol, config, feat_conf, symbol, df)

    if config["save_to_disk"]:
        save_feat(config, feat_conf, feat)
    return feat

def compute_feature(deck, for_symbol, config, feat_conf, symbol, df):
    drop = ['name', 'symbol']
    params = {k:v for k, v in feat_conf.items() if not k in drop}

    feat_name = feat_conf['name']
    if feat_name in ['sector', 'exchange']:
        categories = list(sorted(set(SYMBOLS_CSV[feat_name])))
        category = SYMBOLS_DICT[symbol][feat_name]
        feat = pd.Series(categories.index(category), index=df.index)
    else:
        feat = FEATURES[feat_name](df, **params)

    # Every feature's column is called Close to enable easy recursion
    feat = feat.to_frame("Close")
    return feat


def define_features():
    """Stake out the list of features that is the basis for our features matrix"""
    features = ["log", "ffd_0.5"]

    for d in [50, 250, 500, 1000]:
        for lag in [25, 50, 250, 500, 1000]:
            if lag < d:
                features.append(f"auto_{d}_{lag}")

        features.append(f"stdev_{d}")
        features.append(f"roll_{d}")
        features.append(f"rollimp_{d}")
        features.append(f"amihud_{d}")
        features.append(f"kyle_{d}")
        features.append(f"volratio_{d}")

    return features


def define_feature_configs():
    """Stake out the list of features that is the basis for our features matrix"""
    ffd_f = {"name": "ffd", "d": 0.5}
    features = [
#         {"name": "sector"},
        {"name": "log"},
        {"name": "close", "symbol": 'VIX.XO'},
        ffd_f,
    ]

    for window in [50, 250, 500, 1000]:
        for lag in [25, 50, 250, 500, 1000]:
            if lag < window:
                features.append({"name": "auto", "window": window, "lag": lag})

        features.append({"name": "stdev", "window": window})
        features.append({"name": "roll", "window": window})
        features.append({"name": "rollimp", "window": window})
        features.append({"name": "amihud", "window": window})
        features.append({"name": "kyle", "window": window})
        features.append({"name": "volratio", "com": window})

        # features.append({"name": "stdev", "window": window, "symbol": ffd_f})

    return features