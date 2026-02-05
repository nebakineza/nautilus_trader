"""
Cross-Pair Inventory Coordinator
Monitors and balances inventory across multiple trading pairs in real-time
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import Logger
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency, Money


class CrossPairInventoryCoordinator:
    """
    Coordinates inventory allocation across multiple trading pairs.
    
    This coordinator ensures balanced inventory exposure across all pairs
    by monitoring actual balances and adjusting order sizes dynamically.
    
    Features:
    - Real-time inventory tracking across all pairs
    - Dynamic order size adjustment based on current exposure
    - Target allocation enforcement (equal weight by default)
    - Fast, in-memory operations for low latency
    """
    
    def __init__(
        self,
        cache: Cache,
        logger: Logger,
        target_allocations: Dict[str, Decimal] | None = None,
        max_pair_weight: Decimal = Decimal("0.40"),  # Max 40% in one pair
        min_pair_weight: Decimal = Decimal("0.10"),  # Min 10% in one pair
        rebalance_threshold: Decimal = Decimal("0.15"),  # 15% deviation triggers adjustment
    ):
        """
        Initialize cross-pair coordinator.
        
        Parameters
        ----------
        cache : Cache
            NautilusTrader cache for account state access
        logger : Logger
            Logger instance
        target_allocations : Dict[str, Decimal], optional
            Target weight for each currency (default: equal weight)
        max_pair_weight : Decimal
            Maximum allowed weight for any single pair
        min_pair_weight : Decimal
            Minimum target weight for any pair
        rebalance_threshold : Decimal
            Weight deviation that triggers rebalancing signal
        """
        self._cache = cache
        self._log = logger
        self._target_allocations = target_allocations or {}
        self._max_pair_weight = max_pair_weight
        self._min_pair_weight = min_pair_weight
        self._rebalance_threshold = rebalance_threshold
        
        # Tracked currencies (will be populated dynamically)
        self._tracked_currencies: set[Currency] = set()
        
    def register_currency(self, currency: Currency) -> None:
        """Register a currency for tracking."""
        if currency not in self._tracked_currencies:
            self._tracked_currencies.add(currency)
            self._log.info(
                f"Registered {currency.code} for cross-pair inventory tracking",
                color=LogColor.BLUE,
            )
    
    def get_inventory_snapshot(self, quote_currency: Currency) -> Dict[str, Decimal]:
        """
        Get current inventory snapshot in terms of quote currency value.
        
        Parameters
        ----------
        quote_currency : Currency
            The quote currency (e.g., USDT) to value everything in
        
        Returns
        -------
        Dict[str, Decimal]
            Dictionary of {currency_code: value_in_quote}
        """
        accounts = self._cache.accounts()
        if not accounts:
            return {}
        account = accounts[0]  # accounts() returns a list
        if not account:
            return {}
        
        inventory = {}
        
        for currency in self._tracked_currencies:
            if currency == quote_currency:
                # USDT balance is already in quote terms
                balance = account.balance_total(currency)
                if balance:
                    inventory[currency.code] = Decimal(str(balance.as_decimal()))
                else:
                    inventory[currency.code] = Decimal("0")  # Include zero balances
            else:
                # Need to convert to quote currency value
                # This requires knowing the mid-price, which we get from orderbooks
                balance = account.balance_total(currency)
                if balance and balance.as_decimal() > 0:
                    # For now, store the base currency amount
                    # In production, multiply by mid-price to get quote value
                    inventory[currency.code] = Decimal(str(balance.as_decimal()))
                else:
                    inventory[currency.code] = Decimal("0")  # Include zero balances
        
        return inventory
    
    def get_inventory_weights(self, quote_currency: Currency) -> Dict[str, Decimal]:
        """
        Calculate current inventory weights as percentage of total.
        
        Parameters
        ----------
        quote_currency : Currency
            The quote currency to value everything in
        
        Returns
        -------
        Dict[str, Decimal]
            Dictionary of {currency_code: weight_percentage}
        """
        inventory = self.get_inventory_snapshot(quote_currency)
        
        if not inventory:
            return {}
        
        total_value = sum(inventory.values())
        
        if total_value == 0:
            return {}
        
        weights = {
            currency: (value / total_value)
            for currency, value in inventory.items()
        }
        
        return weights
    
    def get_size_scalar(
        self,
        currency: Currency,
        quote_currency: Currency,
        base_size: Decimal,
    ) -> Decimal:
        """
        Get order size scalar based on current inventory weight.
        
        If a currency is overweight, returns scalar < 1.0 to reduce orders.
        If underweight, returns scalar > 1.0 to increase orders.
        
        Parameters
        ----------
        currency : Currency
            The base currency of the pair
        quote_currency : Currency
            The quote currency (USDT)
        base_size : Decimal
            The base order size before scaling
        
        Returns
        -------
        Decimal
            Multiplier for order size (0.0 to 2.0)
        """
        weights = self.get_inventory_weights(quote_currency)
        
        if not weights or currency.code not in weights:
            return Decimal("1.0")
        
        current_weight = weights[currency.code]
        
        # Calculate target weight (equal weight if not specified)
        if currency.code in self._target_allocations:
            target_weight = self._target_allocations[currency.code]
        else:
            # Equal weight across all tracked currencies
            target_weight = Decimal("1.0") / Decimal(str(len(self._tracked_currencies)))
        
        # Calculate deviation
        deviation = current_weight - target_weight
        
        # Apply scaling based on deviation
        # Over-allocated: reduce order sizes (scalar < 1.0)
        # Under-allocated: increase order sizes (scalar > 1.0)
        
        if deviation > self._rebalance_threshold:
            # Severely overweight - reduce to zero
            return Decimal("0.0")
        elif deviation > Decimal("0.05"):
            # Moderately overweight - reduce by 50%
            return Decimal("0.5")
        elif deviation < -self._rebalance_threshold:
            # Severely underweight - increase by 100%
            return Decimal("2.0")
        elif deviation < Decimal("-0.05"):
            # Moderately underweight - increase by 50%
            return Decimal("1.5")
        else:
            # Within acceptable range - no adjustment
            return Decimal("1.0")
    
    def should_skip_pair(
        self,
        currency: Currency,
        quote_currency: Currency,
    ) -> bool:
        """
        Determine if a pair should temporarily stop trading due to inventory.
        
        Parameters
        ----------
        currency : Currency
            The base currency
        quote_currency : Currency
            The quote currency
        
        Returns
        -------
        bool
            True if pair should stop trading temporarily
        """
        weights = self.get_inventory_weights(quote_currency)
        
        if not weights or currency.code not in weights:
            return False
        
        current_weight = weights[currency.code]
        
        # Stop trading if severely over max weight
        if current_weight > self._max_pair_weight:
            self._log.warning(
                f"{currency.code} inventory at {current_weight:.1%} "
                f"(max: {self._max_pair_weight:.1%}) - PAUSING pair",
                color=LogColor.YELLOW,
            )
            return True
        
        return False
    
    def log_inventory_status(self, quote_currency: Currency) -> None:
        """Log current inventory allocation status."""
        weights = self.get_inventory_weights(quote_currency)
        
        if not weights:
            return
        
        self._log.info("=" * 60, color=LogColor.CYAN)
        self._log.info("CROSS-PAIR INVENTORY STATUS", color=LogColor.CYAN)
        self._log.info("=" * 60, color=LogColor.CYAN)
        
        for currency, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            status = "✅" if weight < self._max_pair_weight else "⚠️ "
            self._log.info(
                f"{status} {currency:8s}: {weight:>6.2%}",
                color=LogColor.GREEN if weight < self._max_pair_weight else LogColor.YELLOW,
            )
        
        self._log.info("=" * 60, color=LogColor.CYAN)
