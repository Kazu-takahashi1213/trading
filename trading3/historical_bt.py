__all__ = ['simulate_pnl', 'estimate_trading_costs', 'SYMBOLS_F', 'SLIPPAGE_ESTIMATE', 'COMMISSION_ESTIMATE']

# Cell

import pandas as pd
import numpy as np
import logging
from .load_data import DATA_DIR

SYMBOLS_F = pd.read_csv(DATA_DIR / "symbols.csv", index_col="iqsymbol")
SLIPPAGE_ESTIMATE = 0.25  # We estimate we'll pay 1/4 of the bid-ask spread
COMMISSION_ESTIMATE = 1

def simulate_pnl(close, signal, pos_size=50000, pos_cap_multi=500):
    pos_cap = pos_size * pos_cap_multi
    volatility = np.log(close).diff().ewm(com=32 * 25).std()
    prices = (np.log(close).diff() / volatility).cumsum()

    currency_pos = (pos_size * signal / volatility).clip(-pos_cap, pos_cap)
    profit = (close.pct_change() * currency_pos.shift(periods=1)).sum(axis=1)
    s_nav, s_nav_wo_costs, s_Profit, s_Profit_wo_costs, stats = estimate_trading_costs(
        close, currency_pos, profit
    )

    return s_nav, s_nav_wo_costs, stats


def estimate_trading_costs(prices, currency_pos, profits, init_capital=7e7):
    # TODO: This code is old and needs refactoring
    # Do copies to shapes of prices dataframe and allow for easy multiplication later
    multipliers = prices.copy()
    for col in multipliers.columns:
        multipliers[col] = SYMBOLS_F.loc[col, "multiplier"]

    tick_sizes = prices.copy()
    for col in tick_sizes.columns:
        tick_sizes[col] = SYMBOLS_F.loc[col, "mintick"]

    commissions = pd.DataFrame(COMMISSION_ESTIMATE, index=prices.index, columns=prices.columns)

    num_contracts = currency_pos.div(multipliers.mul(prices)).round(0)

    contracts_traded = num_contracts.diff().abs()
    slippage = contracts_traded.mul(tick_sizes.mul(multipliers)) * SLIPPAGE_ESTIMATE
    commissions_cost = contracts_traded.mul(commissions)
    trading_costs = commissions_cost + slippage
    daily_trading_costs = trading_costs.fillna(0).sum(axis=1)
    profits_with_costs = profits - daily_trading_costs

    nav_without_costs = (1 + profits / init_capital).cumprod()
    nav_with_costs = (1 + (profits_with_costs) / init_capital).cumprod()
    trade_count = contracts_traded.astype(bool).astype(float).sum().sum()

    stats = {
        "trade_count": trade_count.sum().sum(),
        "contracts_traded": contracts_traded.sum().sum(),
        "total_trading_costs": daily_trading_costs.sum(),
        "commissions_cost": commissions_cost.sum(axis=1).sum(),
        "slippage": slippage.sum(axis=1).sum(),
    }

    return nav_with_costs, nav_without_costs, profits_with_costs, profits, stats