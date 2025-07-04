
__all__ = ['DATA_DIR', 'F_PAYLOAD_DIR', 'DAILY_DATA_DIR', 'SYMBOLS_CSV', 'get_symbols', 'load_contract',
           'load_contracts', 'load_all_cont_contracts', 'get_data', 'process_bars', 'load_and_sample_bars',
           'determine_bar_size', 'feat_safe_name', 'load_hdf', 'save_hdf', 'bars_path', 'events_b_path', 'feats_path',
           'feat_path', 'imp_path', 'payload_path', 'load_bars', 'save_bars', 'load_events_b', 'save_events_b',
           'load_feat', 'save_feat', 'load_imp', 'save_imp', 'load_payload', 'save_payload']

# Cell

import seaborn as sn
import pandas as pd
import json
import logging
from path import Path
from dateutil.relativedelta import relativedelta
from mlfinlab.data_structures import get_dollar_bars, get_tick_bars, get_volume_bars

from .utils import NumpyEncoder

# You'll likely have to change these if you're intending to run the code yourself
# TODO: Factor out into settings.py file
DATA_DIR = Path("~/Dropbox/algotrading/data").expanduser()
F_PAYLOAD_DIR = Path("~/pr/fincl/frontend/public/payloads").expanduser()
DAILY_DATA_DIR = DATA_DIR / "daily"

SYMBOLS_CSV = pd.read_csv(DATA_DIR / "symbols.csv", index_col="iqsymbol")


# Cell

def get_symbols(symbol_groups):
    lists = {"us_index": ["@NQ#C", "@ES#C", "@YM#C"]}
    if len(symbol_groups) == 1 and symbol_groups[0] in lists:
        return lists[symbol_groups[0]]

    sectors = {
        "agriculture": "Agriculture",
        "currency": "Currency",
        "energy": "Energy",
        "equity_index": "Equity Index",
        "interest_rate": "Interest Rate",
        "metals": "Metals",
    }
    symbol_groups = [sectors[x] for x in symbol_groups]
    picked = SYMBOLS_CSV[SYMBOLS_CSV["Sector"].isin(symbol_groups)]

    ignore = ["@LH#C"]
    return [x for x in picked.index.values if x not in ignore]


def load_contract(contract_name, directory):
    series = pd.read_csv(
        DATA_DIR / directory / "{}.csv".format(contract_name), index_col=0
    )
    series = series[::-1]
    if directory == "minutely":
        series["Time"] = series["date"] + " " + series["time"]
        series = series.set_index(
            pd.to_datetime(series["Time"], format="%Y-%m-%d 0 days %H:%M:00.000000000")
        )
    else:
        series["Time"] = series["date"]
        series = series.set_index(pd.to_datetime(series["Time"], format="%Y-%m-%d"))

    series = series[["open_p", "close_p", "prd_vlm", "Time"]]
    series = series.rename(
        columns={"close_p": "Close", "open_p": "Open", "prd_vlm": "Volume"}
    )
    series["Instrument"] = contract_name
    return series


def load_contracts(symbol, directory="minutely", start_date=None, end_date=None):
    contract_names = [
        x.basename().namebase
        for x in (DATA_DIR / directory).files("*{}*".format(symbol))
    ]
    loaded = [load_contract(x, directory) for x in contract_names]
    loaded = list(sorted(loaded, key=lambda x: x.index[-1]))
    first = loaded[0]
    # cut out from later contracts what former contracts already have
    zipped = zip(loaded, loaded[1:])
    cut_contracts = [
        latter.truncate(before=former.index[-1] + pd.Timedelta(minutes=1))
        for former, latter in zipped
    ]

    concatted = pd.concat([first] + cut_contracts)
    return concatted.truncate(before=start_date, after=end_date)


def load_all_cont_contracts():
    all_continuous_contracts = DAILY_DATA_DIR.files("*#C*")
    all_continuous_contracts = [x.basename().namebase for x in all_continuous_contracts]
    return {name: load_contract(name, "daily") for name in all_continuous_contracts}


def get_data(symbol, frequency, start_date, end_date):
    # Include up to 1 year prior for feature engineering
    # we blindly assume no code wants a longer warm-up period than that
    return load_contracts(
        symbol,
        frequency,
        start_date - relativedelta(years=1) if start_date else None,
        end_date,
    )

def process_bars(bars, size, fun):
    # Renaming our bar columns & format for mlfinlab for processing and then back into our original format
    # OHL from 1-min bars are ignored
    bars = bars[['Close', 'Volume']].reset_index()
    bars.columns = ['date_time', 'close', 'volume']
    s_bars = fun(bars, threshold=size)
    bars = s_bars[['date_time', 'open', 'high', 'low', 'close', 'volume', 'cum_dollar_value', 'cum_ticks', 'cum_buy_volume']]
    bars.columns = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Dollar Volume', 'Num Ticks', 'Buy Volume']
    bars = bars.set_index('Time', drop=False)
    return bars


def load_and_sample_bars(symbol, start_date, end_date, type_, size=None):
    bars = get_data(symbol, "minutely", start_date, end_date)
    bars["Dollar Volume"] = bars["Volume"] * bars["Close"]

    if size is None:
        size = determine_bar_size(bars, type_)

    process_bars_fun = {
        "time": get_tick_bars,
        "volume": get_volume_bars,
        "dollar": get_dollar_bars,
    }[type_]

    return process_bars(bars, size, process_bars_fun), size


# Cell
def determine_bar_size(bars, bar_type):
    # Return bar size to have approx. 25 bars per day for the year 2019
    col = {"dollar": "Dollar Volume", "volume": "Volume"}[bar_type]
    bar_size = bars[bars.index.year == 2019][col].sum() / 252 / 25
    return bar_size

# Cell

def feat_safe_name(feat_c):
    fc = feat_c.copy()
    name = fc.pop("name")
    dumped = json.dumps(fc, sort_keys=True, separators=(',', '_')).replace('"', '')
    return f"{name}_{dumped}"

def load_hdf(path):
    if path.exists():
        return pd.read_hdf(path, 'table')


def save_hdf(obj, path):
    obj.to_hdf(path, 'table')
    return path


def bars_path(symbol, c):
    return DATA_DIR / c['bar_type'] / f"{symbol}_bars.h5"


def events_b_path(symbol, c):
    return DATA_DIR / c['bar_type'] / f"{symbol}_events_{c['vol_estimate']}_{c['binarize']}_{c['binarize_params']}_{c['downsampling']}.h5"


def feats_path(symbol, c):
    feat_names = '-'.join(sorted(set(x.split('_')[0] for x in c['features'])))
    return DATA_DIR / c['bar_type'] / f"{symbol}_feats_{feat_names}.h5"


def feat_path(c, feat_c):
    # Make a compact, unique path for this feature config
    basename = feat_safe_name(feat_c)
    return DATA_DIR / 'features' / c['bar_type'] / f"{basename}.h5"


def imp_path(symbol, c):
    # TODO: This ignores feature paramters
    feat_names = '-'.join(sorted(set(x['name'] for x in c['features'])))
    return DATA_DIR / c['bar_type'] / f"{symbol}_fimp_{c['binarize']}_{c['binarize_params']}_{c['alpha']}_{c['alpha_params']}_{feat_names}_{c['feat_imp_method']}.h5"


def payload_path(symbols, c):
    symbols_s = '-'.join(c['symbol_groups'] or c['symbols'])
    return DATA_DIR / 'payloads' / f"payload_{symbols_s}_{c['bar_type']}_{c['binarize']}_{c['binarize_params']}_{c['alpha']}_{c['alpha_params']}_{c['classifier']}.json"

###


def load_bars(symbol, config):
    if config["load_from_disk"]:
        path = bars_path(symbol, config)
        return load_hdf(path)


def save_bars(symbol, config, bars):
    if config["save_to_disk"]:
        path = bars_path(symbol, config)
        return save_hdf(bars, path)


def load_events_b(symbol, config):
    if config["load_from_disk"]:
        path = events_b_path(symbol, config)
        return load_hdf(path)


def save_events_b(symbol, config, events_b):
    if config["save_to_disk"]:
        path = events_b_path(symbol, config)
        return save_hdf(events_b, path)


def load_feat(config, feat_config):
    if config["load_from_disk"]:
        path = feat_path(config, feat_config)
        return load_hdf(path)


def save_feat(config, feat_config, feat):
    if config["save_to_disk"]:
        path = feat_path(config, feat_config)
        return save_hdf(feat, path)


def load_imp(symbol, config):
    if config["load_from_disk"]:
        path = imp_path(symbol, config)
        return load_hdf(path)


def save_imp(symbol, config, imp):
    if config["save_to_disk"]:
        path = imp_path(symbol, config)
        return save_hdf(imp, path)


def load_payload(symbols, config):
    if config["load_from_disk"]:
        path = payload_path(symbols, config)
        try:
            if path.exists() and path.size:
                with open(path) as f:
                    return json.load(f)
        except:
            logging.error(f"corrupted payload: {path}")


def save_payload(symbols, config, payload):
    if config["save_to_disk"]:
        path = payload_path(symbols, config)
        with open(path, 'w') as f:
            json.dump(payload, f, cls=NumpyEncoder)
        return path