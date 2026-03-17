import os
from dataclasses import dataclass
from dotenv import load_dotenv


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class Config:
    # Polymarket CLOB
    poly_clob_host: str
    poly_ws_url: str
    poly_user_ws_url: str

    # API creds
    poly_api_key: str
    poly_api_secret: str
    poly_api_passphrase: str

    # Wallet / signing
    poly_private_key: str
    poly_funder: str
    poly_signature_type: int
    poly_chain_id: int

    # Market tokens
    yes_token_id: str
    no_token_id: str
    condition_id: str
    yes_is_up: bool

    # Strategy
    high_price_target: float
    high_price_min: float
    high_price_max: float
    other_side_price: float
    order_usdc_high: float
    order_usdc_other: float
    prob_threshold: float
    prob_scale: float
    t_minus_seconds: int
    requote_interval_ms: int
    replace_target_ms: int
    dry_run: bool

    # Risk
    capital_usdc: float
    max_position_pct: float
    stop_loss_pct: float
    auto_merge: bool
    merge_min_shares: float

    # Binance
    binance_symbol: str
    binance_ws_url: str

    # DB
    db_dsn: str
    db_write: bool

    # Logging
    log_level: str
    log_file: str

    # CTF merge
    polygon_rpc_url: str
    ctf_contract_address: str
    usdc_contract_address: str

    @staticmethod
    def load() -> "Config":
        load_dotenv()

        binance_symbol = os.getenv("BINANCE_SYMBOL", "btcusdt").lower()
        default_binance_ws = f"wss://stream.binance.com:9443/ws/{binance_symbol}@kline_5m"

        return Config(
            poly_clob_host=os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com"),
            poly_ws_url=os.getenv(
                "POLY_WS_URL",
                "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            ),
            poly_user_ws_url=os.getenv(
                "POLY_USER_WS_URL",
                "wss://ws-subscriptions-clob.polymarket.com/ws/user",
            ),
            poly_api_key=os.getenv("POLY_API_KEY", ""),
            poly_api_secret=os.getenv("POLY_API_SECRET", ""),
            poly_api_passphrase=os.getenv("POLY_API_PASSPHRASE", ""),
            poly_private_key=os.getenv("POLY_PRIVATE_KEY", ""),
            poly_funder=os.getenv("POLY_FUNDER", ""),
            poly_signature_type=_get_int("POLY_SIGNATURE_TYPE", 0),
            poly_chain_id=_get_int("POLY_CHAIN_ID", 137),
            yes_token_id=os.getenv("YES_TOKEN_ID", ""),
            no_token_id=os.getenv("NO_TOKEN_ID", ""),
            condition_id=os.getenv("CONDITION_ID", ""),
            yes_is_up=_get_bool("YES_IS_UP", True),
            high_price_target=_get_float("HIGH_PRICE_TARGET", 0.92),
            high_price_min=_get_float("HIGH_PRICE_MIN", 0.90),
            high_price_max=_get_float("HIGH_PRICE_MAX", 0.95),
            other_side_price=_get_float("OTHER_SIDE_PRICE", 0.05),
            order_usdc_high=_get_float("ORDER_USDC_HIGH", 50.0),
            order_usdc_other=_get_float("ORDER_USDC_OTHER", 10.0),
            prob_threshold=_get_float("PROB_THRESHOLD", 0.85),
            prob_scale=_get_float("PROB_SCALE", 1.0),
            t_minus_seconds=_get_int("T_MINUS_SECONDS", 10),
            requote_interval_ms=_get_int("REQUOTE_INTERVAL_MS", 50),
            replace_target_ms=_get_int("REPLACE_TARGET_MS", 100),
            dry_run=_get_bool("DRY_RUN", True),
            capital_usdc=_get_float("CAPITAL_USDC", 1000.0),
            max_position_pct=_get_float("MAX_POSITION_PCT", 0.20),
            stop_loss_pct=_get_float("STOP_LOSS_PCT", 0.02),
            auto_merge=_get_bool("AUTO_MERGE", False),
            merge_min_shares=_get_float("MERGE_MIN_SHARES", 1.0),
            binance_symbol=binance_symbol,
            binance_ws_url=os.getenv("BINANCE_WS_URL", default_binance_ws),
            db_dsn=os.getenv(
                "DB_DSN", "postgresql://polymarket:polymarket@localhost:5432/polymarket"
            ),
            db_write=_get_bool("DB_WRITE", True),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", "polymarket_bot.log"),
            polygon_rpc_url=os.getenv("POLYGON_RPC_URL", ""),
            ctf_contract_address=os.getenv("CTF_CONTRACT_ADDRESS", ""),
            usdc_contract_address=os.getenv("USDC_CONTRACT_ADDRESS", ""),
        )