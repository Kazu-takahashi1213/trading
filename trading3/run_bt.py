__all__ = ['downsample', 'alpha', 'join_importances', 'pick_good_features', 'combine_symbol_decks', 'train_test_split',
           'binarize', 'prepare_payload', 'get_symbols_list', 'abort_early', 'parse_config', 'FORMAT', 'SYMBOL_GROUPS',
           'load_sample_and_binarize', 'run_feature_engineering', 'prepare_alpha_bins_feature_imps', 'run_ml_pipe',
           'IGNORE_SYMBOLS', 'run_bt']

# Cell
import numpy as np
import pandas as pd
import logging
import random
from datetime import date
from dateutil.relativedelta import relativedelta

from .load_data import (
    get_symbols,
    load_and_sample_bars,
    load_bars,
    save_bars,
    load_events_b,
    save_events_b,
    feat_safe_name,
    load_feat,
    save_feat,
    load_imp,
    save_imp,
    load_payload,
    save_payload,
)
from .filters import cusum
from .multiprocess import mp_pandas_obj
from .utils import get_daily_vol, NumpyEncoder
from .get_bins import get_bins, drop_labels
from .alpha import ma_alpha, bb_alpha
from .binarize import triple_barrier_method, fixed_horizon
from .feature_eng import engineer_feature, define_feature_configs
from .reporting import get_reports
from .models import get_model
from .feature_importance import feat_importance

FORMAT = "%(asctime)-15s %(message)s"
logging.basicConfig(format=FORMAT, level=logging.DEBUG)

SYMBOL_GROUPS = {
    "agriculture": "Agriculture",
    "currency": "Currency",
    "energy": "Energy",
    "equity_index": "Equity Index",
    "interest_rate": "Interest Rate",
    "metals": "Metals",
}


def downsample(bars, type_, daily_vol):
    if type_ == "cusum":
        return cusum(bars["Close"], daily_vol.mean())

    return bars.index


def alpha(bars, events, type_, params):
    if type_ == "none":
        return events
    elif type_ == "ma-cross":
        signal = ma_alpha(bars, *params)
    elif type_ == "bbands-mr":
        signal = bb_alpha(bars, *params, True)
    elif type_ == "bbands-tf":
        signal = bb_alpha(bars, *params, False)

    events["side"] = signal

    assert set(events["side"].dropna()) == set([1, -1]), set(events["side"].dropna())
    return events

def join_importances(deck):
    """Join the feature importances computed parallelized & per-symbol into one dataframe"""
    dfs = [x['imp'] for x in deck.values()]
    mean = pd.concat([x["mean"] for x in dfs], axis=1).mean(axis=1)
    std = pd.concat([x["std"] for x in dfs], axis=1).std(axis=1) * len(dfs) ** -0.5

    return pd.DataFrame({"mean": mean, "std": std})


def pick_good_features(imp_all, columns, method):
    """Pick features that help our classifier's predictive abilities"""
    imp_d = imp_all["mean"].to_dict()
    cutoff = 0 if method == "MDA" else imp_all["mean"].mean()
    cols = [col for col in columns if imp_d[col] > cutoff]
    logging.info(f"Picked {len(cols)}/{len(columns)} important features: {cols}")

    return cols


def combine_symbol_decks(deck):
    """
    Join events, features and bins that have been computed on a per-symbol level into one
    grand data-frame. To note: In order to in the future still be able to differentiate which row belongs
    to which symbol we embed the symbols position in our symbols table into the microseconds of the index.
    This is not in any way good code, but it allows us to still have a unique & sortable index without
    resorting to multi-indices or the like. This is predicated on the fact that we know we only sample from
    1-minute bars. A.k.a Poor Man's Multi-Index
    """
    e_x_ys = {}
    for i, (symbol, symbol_deck) in enumerate(deck.items()):
        e_x_y = symbol_deck['e_x_y']
        events_train, X_train, y_train, events_test, X_test, y_test = e_x_y
        y_train = y_train.to_frame()
        y_test = y_test.to_frame()

        # every row for every symbol has a unique datetime index and is sortable
        for df in [events_train, X_train, y_train, events_test, X_test, y_test]:
            df.index += pd.Timedelta(i, "us")

        events_train["t1"] += pd.Timedelta(i, "us")
        events_test["t1"] += pd.Timedelta(i, "us")

        e_x_ys[symbol] = (events_train, X_train, y_train, events_test, X_test, y_test)

    grand_frames = []
    for list_of_dfs in zip(*e_x_ys.values()):
        grand_frame = pd.concat(list_of_dfs)
        grand_frame = grand_frame.sort_index()
        grand_frames.append(grand_frame)

    return grand_frames


def train_test_split(bars, events, feats, bins, start_date=None, end_date=None):
    """
    Exclude rows from our engineered features which haven't completed the warmup for all feature columns
    and split the set 50/50 into train & test set
    """
    X_all = feats
    y_all = bins["bin"]
    y_all = bins

    # Drop all rows where we don't have a complete set of features
    merged = pd.merge(X_all, y_all, left_index=True, right_index=True).dropna()
    merged = merged.truncate(before=start_date, after=end_date)

    X_all = merged.drop(columns=bins.columns)
    y_all = merged["bin"]

    events_all = events[events.index.isin(X_all.index)]
    # Store all-kinds-of-information in events for later PnL calculations
    events_all[bins.columns] = merged[bins.columns]
    events_all["close_p"] = bars["Close"][bars.index.isin(events.index)]

    cut = X_all.shape[0] // 2
    events_train, events_test = events_all.iloc[:cut], events_all.iloc[cut:]
    X_train, X_test = X_all.iloc[:cut], X_all.iloc[cut:]
    y_train, y_test = y_all.iloc[:cut], y_all.iloc[cut:]

    logging.info(f"bars {bars.shape}, events {events.shape}, feats {feats.shape}, bins {bins.shape}, X_all {X_all.shape}, X_train {X_train.shape}")

    return (events_train, X_train, y_train, events_test, X_test, y_test)


def binarize(bars, t_events, type_, binarize_params, daily_vol, num_threads):
    """
    Binarize the rows, i.e. for every row determine a forward returns window which
    is then used to calculate that row's label
    """
    if type_ == "fixed_horizon":
        return fixed_horizon(t_events, binarize_params)
    elif type_ == "triple_barrier_method":
        return triple_barrier_method(
            bars, t_events, binarize_params, daily_vol, num_threads
        )



def prepare_payload(config, symbols, imp_all, reports):
    """Prepare payload for serialization"""
    config["start_date"] = config["start_date"].isoformat()
    config["end_date"] = config["end_date"].isoformat()

    return {
        "symbols": symbols,
        "feature_importance": imp_all.to_dict(),
        "config": config,
        **reports,
    }


def get_symbols_list(config):
    if config["symbols"]:
        symbols = config["symbols"]
    else:
        symbols = get_symbols(config["symbol_groups"])

    symbols = [x for x in symbols if x not in IGNORE_SYMBOLS]
    return symbols


def abort_early(config):
    if config["check_completed"]:
        symbols = get_symbols_list(config)
        payload = load_payload(symbols, config)
        if payload is not None:
            logging.info("We have the payload, not recomputing")
            return True
    return False


def parse_config(data):
    """Turn the input parameters into the config object which is used as configuration throughout the project"""
    alpha, *alpha_params = data["alpha"].split("_")

    alpha_params = [float(x) if "." in x else int(x) for x in alpha_params]

    default_binarize_params = {"triple_barrier_method": [1, 1, 1], "fixed_horizon": 100}
    binarize_params = data.get("binarize_params") or default_binarize_params[data["binarize"]]

    return {
        "start_date": data.get("start_date", date(2000, 1, 1)),
        "end_date": data.get("end_date", date(2020, 1, 1)),
        "vol_estimate": 100,
        "downsampling": "cusum",
        "symbols": data.get("symbols"),
        "symbol_groups": data.get("symbol_groups"),
        "test_procedure": "walk_forward",
        "classifier": data["classifier"],
        "bar_type": data["bar_type"],
        "bar_size": None,
        "binarize": data["binarize"],
        "binarize_params": binarize_params,
        "alpha": alpha,
        "alpha_params": alpha_params,
        "feature_calc_only": data.get("feature_calc_only", False),
        "feature_imp_only": data.get("feature_imp_only", False),
        "skip_feature_imp": data.get("skip_feature_imp", False),
        "reuse_hypers": data.get("reuse_hypers", True),
        "hypers_n_iter": data.get("hypers_n_iter", 25),
        "load_from_disk": data.get("load_from_disk", True),
        "save_to_disk": data.get("save_to_disk", True),
        "optimize_hypers": data.get("optimize_hypers", True),
        "feat_imp_method": data.get("feat_imp_method", "MDA"),
        "feat_imp_cv": data.get("feat_imp_cv", 5),
        "num_threads": data.get("num_threads", 32),
        "n_jobs": data.get("n_jobs", 4),
        "check_completed": data.get("check_completed", False),
    }



# Cell

# TODO: Figure out why Lean Hogs break our code
IGNORE_SYMBOLS = ["@LH#C"]


def load_sample_and_binarize(config):
    """
    Load our bars, chunk them into dollar bars aiming to have 50 bars per day per symbol for the year 2019.
    These bars are then CUSUM downsampled and binarized before being saved for later runs.
    """
    symbols = get_symbols_list(config)

    logging.info(f"Symbols: {symbols}")
    deck = {}
    for symbol in symbols:
        bars = load_bars(symbol, config)
        if bars is None:
            bars, bar_size = load_and_sample_bars(symbol, config["start_date"], config["end_date"], config["bar_type"])
            save_bars(symbol, config, bars)

        events_b = load_events_b(symbol, config)
        if events_b is None:
            daily_vol = get_daily_vol(bars["Close"], config["vol_estimate"])
            t_events = downsample(bars, config["downsampling"], daily_vol)
            logging.info(f"{symbol}: Downsampled from {len(bars)} to {len(t_events)}")

            logging.debug(f"{symbol}: Binarize {config['binarize']}")
            events_b = binarize(
                bars,
                t_events,
                config["binarize"],
                config["binarize_params"],
                daily_vol,
                config["num_threads"],
            )

            save_events_b(symbol, config, events_b)

        logging.info(f"{symbol}: Have {bars.shape[0]} bars and {events_b.shape[0]} binarized events")
        deck[symbol] = {'bars': bars, 'events_b': events_b}

    return deck


def run_feature_engineering(config, deck):
    """Load already-engineered features or engineer if we can't"""
    for symbol, symbol_deck in deck.items():
        logging.debug(f"{symbol}: Feature engineering")
        bars = symbol_deck['bars']
        feats = []
        for feat_config in config["features"]:
            # We pass a copy in so the feat_eng code can modify that to its hearts content,
            # while for us the information remains non-redundant
            feat = engineer_feature(deck, symbol, config, feat_config.copy())["Close"]
            feat.name = feat_safe_name(feat_config)
            feats.append(feat)
        feats2 = pd.concat(feats, axis=1)
        # Reindex in case of outside feats
        feats3 = feats2.reindex(index=deck[symbol]['bars'].index)
        deck[symbol]['feats'] = feats3
    return deck


def prepare_alpha_bins_feature_imps(config, deck):
    for symbol, symbol_deck in deck.items():
        logging.debug(f"{symbol}: Get bins and feature imps")

        bars, events_b, feats = symbol_deck['bars'], symbol_deck['events_b'], symbol_deck['feats']
        events = alpha(
            bars, events_b, config["alpha"], config["alpha_params"]
        )

        bins = get_bins(events, bars["Close"])
        bins = drop_labels(bins)

        e_x_y = train_test_split(
            bars,
            events,
            feats,
            bins,
            config["start_date"],
            config["end_date"],
        )
        events_train, X_train, y_train, events_test, X_test, y_test = e_x_y

        if config['skip_feature_imp']:
            imp = {}
        else:
            imp = load_imp(symbol, config)
            if imp is None:
                imp = feat_importance(
                    events_train,
                    X_train,
                    y_train,
                    cv=config["feat_imp_cv"],
                    method=config["feat_imp_method"],
                    num_threads=config["num_threads"],

                )
                save_imp(symbol, config, imp)

        deck[symbol] = {'imp': imp, 'e_x_y': e_x_y}

    return deck


def run_ml_pipe(config, deck):
    """
    Run the large chunk of our ML pipeline, which includes calculating the primary (and secondary) models,
    splitting our data into train/test sets, calulating feature importances, hyper-parameter optimization,
    model fitting and evaluation and generation of final reports which are later user for PnL simulations.
    """
    if config["feature_imp_only"]:
        return

    symbols = list(deck.keys())
    for symbol, symbol_deck in deck.items():
        logging.debug(f"{symbol} {[x.shape for x in symbol_deck['e_x_y']]}")

    grand_frames = combine_symbol_decks(deck)
    events_train, X_train, y_train, events_test, X_test, y_test = grand_frames
    y_train, y_test = y_train["bin"], y_test["bin"]

    if config["skip_feature_imp"]:
        imp_all = pd.DataFrame()
    else:
        # Important feats
        imp_all = join_importances(deck)
        cols = pick_good_features(imp_all, X_train.columns, config["feat_imp_method"])
        X_train, X_test = X_train[cols], X_test[cols]

    del deck

    hyper_params = None
    # Try loading the payload so we can re-use hyper parameters from previous run
    payload = load_payload(symbols, config)
    if payload is not None:
        if config["reuse_hypers"]:
            report = payload["secondary"] or payload["primary"]
            hyper_params = report["hyper_params"]
            logging.info(f"Loaded hypers {hyper_params}")

    model, hyper_params = get_model(
        events_train,
        X_train,
        y_train,
        config["classifier"],
        config["optimize_hypers"],
        config["hypers_n_iter"],
        config["num_threads"],
        config["n_jobs"],
        hyper_params,
    )

    reports = get_reports(
        model,
        events_test,
        X_train,
        y_train,
        X_test,
        y_test,
        config["test_procedure"],
        config["alpha"] != "none",
        hyper_params,
    )

    saved_path = ""
    payload = prepare_payload(config, symbols, imp_all, reports)
    saved_path = save_payload(symbols, config, payload)

    return saved_path

# Cell

def run_bt(**data):
    config = parse_config(data)
    config['features'] = define_feature_configs()
    logging.info(f"config: {config}")

    if abort_early(config):
        return ''

    # We store every symbol's data and computations in a central "deck" dictionary
    deck = load_sample_and_binarize(config)

    deck = run_feature_engineering(config, deck)
    if config['feature_calc_only']:
        return ''

    deck = prepare_alpha_bins_feature_imps(config, deck)
    payload_path = run_ml_pipe(config, deck)
    return payload_path