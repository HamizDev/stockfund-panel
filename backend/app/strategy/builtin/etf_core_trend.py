"""ETF broad index / bond / gold trend research."""
from _etf_presets import ETFTrendStrategy, strategy_meta

META = strategy_meta("core")
# This is an explicit fund pool, not the engine's default stock screen.
BASIC_FILTER = {"price_min": None, "price_max": None, "market_cap_min": None,
                "amount_min": None, "exclude_new_days": 0, "boards": None, "exclude_st": False}
EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_etf_trend_entry"]
EXIT_SIGNALS = ["signal_etf_trend_exit"]
MATRIX_STRATEGY = ETFTrendStrategy("core")
