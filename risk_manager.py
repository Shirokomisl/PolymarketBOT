import asyncio
from dataclasses import dataclass
from typing import Dict, Optional

from web3 import Web3


@dataclass
class Position:
    size: float = 0.0
    avg_price: Optional[float] = None


class CTFMerger:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self._w3: Optional[Web3] = None

    def _get_w3(self) -> Optional[Web3]:
        if not self.config.polygon_rpc_url:
            return None
        if self._w3 is None:
            self._w3 = Web3(Web3.HTTPProvider(self.config.polygon_rpc_url))
        return self._w3

    async def merge_full_sets(self, condition_id: str, amount_usdc: float) -> Optional[str]:
        if not self.config.poly_private_key:
            self.logger.warning("Merge: нет private key")
            return None
        if not (self.config.ctf_contract_address and self.config.usdc_contract_address):
            self.logger.warning("Merge: нет адресов контрактов")
            return None

        w3 = self._get_w3()
        if w3 is None:
            self.logger.warning("Merge: нет RPC URL")
            return None

        # Минимальный ABI для mergePositions
        abi = [
            {
                "constant": False,
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "partition", "type": "uint256[]"},
                    {"name": "amount", "type": "uint256"},
                ],
                "name": "mergePositions",
                "outputs": [],
                "type": "function",
            }
        ]

        account = w3.eth.account.from_key(self.config.poly_private_key)
        ctf = w3.eth.contract(address=Web3.to_checksum_address(self.config.ctf_contract_address), abi=abi)

        condition_bytes = Web3.to_bytes(hexstr=condition_id)
        parent_collection = b"\x00" * 32
        partition = [1, 2]
        amount = int(amount_usdc * 1_000_000)

        tx = ctf.functions.mergePositions(
            Web3.to_checksum_address(self.config.usdc_contract_address),
            parent_collection,
            condition_bytes,
            partition,
            amount,
        )

        def _send() -> str:
            nonce = w3.eth.get_transaction_count(account.address)
            gas_price = w3.eth.gas_price
            built = tx.build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                    "chainId": self.config.poly_chain_id,
                }
            )
            # Оценка газа
            built["gas"] = w3.eth.estimate_gas(built)
            signed = w3.eth.account.sign_transaction(built, self.config.poly_private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            return tx_hash.hex()

        try:
            return await asyncio.to_thread(_send)
        except Exception as exc:
            self.logger.warning("Merge ошибка: %s", exc)
            return None


class RiskManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.positions: Dict[str, Position] = {"YES": Position(), "NO": Position()}
        self.reserved_usdc: float = 0.0
        self.last_signal_direction: Optional[str] = None
        self.last_signal_btc_price: Optional[float] = None
        self.merger = CTFMerger(config, logger)

    def can_place_order(self, order_usdc: float) -> bool:
        max_per_trade = self.config.capital_usdc * self.config.max_position_pct
        if order_usdc > max_per_trade:
            self.logger.warning("Размер ордера %.2f больше лимита %.2f", order_usdc, max_per_trade)
            return False
        if self.reserved_usdc + order_usdc > self.config.capital_usdc:
            self.logger.warning("Недостаточно капитала: зарезервировано %.2f", self.reserved_usdc)
            return False
        return True

    def set_reserved(self, total_usdc: float) -> None:
        self.reserved_usdc = total_usdc

    def update_signal(self, direction: str, btc_price: float) -> None:
        self.last_signal_direction = direction
        self.last_signal_btc_price = btc_price

    def check_stop_loss(self, current_btc_price: float) -> bool:
        if self.last_signal_direction is None or self.last_signal_btc_price is None:
            return False
        if self.last_signal_direction == "UP":
            if current_btc_price <= self.last_signal_btc_price * (1 - self.config.stop_loss_pct):
                return True
        else:
            if current_btc_price >= self.last_signal_btc_price * (1 + self.config.stop_loss_pct):
                return True
        return False

    async def on_trade(self, outcome: str, side: str, price: float, size: float) -> None:
        pos = self.positions[outcome]
        if side == "BUY":
            new_size = pos.size + size
            if pos.avg_price is None:
                pos.avg_price = price
            else:
                pos.avg_price = (pos.avg_price * pos.size + price * size) / max(new_size, 1e-9)
            pos.size = new_size
        else:
            pos.size = max(pos.size - size, 0)
            if pos.size == 0:
                pos.avg_price = None

        if self.config.auto_merge:
            await self.maybe_merge()

    async def maybe_merge(self) -> None:
        yes = self.positions["YES"].size
        no = self.positions["NO"].size
        amount = min(yes, no)
        if amount < self.config.merge_min_shares:
            return
        if not self.config.condition_id:
            self.logger.warning("Merge: не задан CONDITION_ID")
            return
        tx_hash = await self.merger.merge_full_sets(self.config.condition_id, amount)
        if tx_hash:
            self.logger.info("Merge tx: %s", tx_hash)
            self.positions["YES"].size -= amount
            self.positions["NO"].size -= amount
            if self.positions["YES"].size <= 0:
                self.positions["YES"].size = 0
                self.positions["YES"].avg_price = None
            if self.positions["NO"].size <= 0:
                self.positions["NO"].size = 0
                self.positions["NO"].avg_price = None
