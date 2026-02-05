#!/usr/bin/env python3
"""
Analyze Spot Trading P&L from Bot Logs

Parses NautilusTrader logs and calculates realized P&L from spot trades only.
Excludes unrealized P&L and market movement effects.

Usage:
    python scripts/analyze_spot_pnl.py [--log-file PATH] [--since TIMESTAMP]
"""

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Fill:
    """Represents an order fill"""
    timestamp: str
    instrument: str
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_currency: str
    
    @property
    def notional_value(self) -> Decimal:
        return self.quantity * self.price
    
    @property
    def commission_usdt(self) -> Decimal:
        """Convert commission to USDT equivalent"""
        if self.commission_currency == "USDT":
            return self.commission
        # For base currency fees, approximate USDT value using fill price
        return self.commission * self.price


@dataclass
class PositionClose:
    """Represents a closed position"""
    timestamp: str
    instrument: str
    realized_pnl: Decimal
    

class SpotPnLAnalyzer:
    """Analyzes spot trading P&L from logs"""
    
    def __init__(self):
        self.fills: List[Fill] = []
        self.position_closes: List[PositionClose] = []
        
    def parse_log_file(self, log_path: Path, since: Optional[str] = None):
        """Parse log file and extract trading events"""
        
        fill_pattern = re.compile(
            r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z).*OrderFilled\('
            r'.*instrument_id=([A-Z]+USDT-SPOT\.BYBIT)'
            r'.*order_side=([A-Z]+)'
            r'.*last_qty=([0-9._]+)'
            r'.*last_px=([0-9._]+) USDT'
            r'.*commission=([0-9.]+) ([A-Z]+)'
        )
        
        position_pattern = re.compile(
            r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z).*PositionClosed\('
            r'.*instrument_id=([A-Z]+USDT-SPOT\.BYBIT)'
            r'.*?realized_pnl=(-?[0-9.]+) USDT'
        )
        
        # Normalize since timestamp for comparison (use first 23 chars for consistent comparison)
        since_normalized = since[:23] if since else None
        
        with open(log_path, 'r') as f:
            for line in f:
                # Extract timestamp from start of line
                if len(line) < 24:
                    continue
                
                line_time = line[:23]  # '2026-02-04T20:15:36.687' (23 chars)
                
                # Filter by timestamp if requested
                if since_normalized:
                    if line_time < since_normalized:
                        continue
                
                # Parse fills
                fill_match = fill_pattern.search(line)
                if fill_match:
                    timestamp, instrument, side, qty, price, commission, comm_ccy = fill_match.groups()
                    
                    self.fills.append(Fill(
                        timestamp=timestamp,
                        instrument=instrument.replace('-SPOT.BYBIT', ''),
                        side=side,
                        quantity=Decimal(qty),
                        price=Decimal(price),
                        commission=Decimal(commission),
                        commission_currency=comm_ccy,
                    ))
                
                # Parse position closes
                pos_match = position_pattern.search(line)
                if pos_match:
                    timestamp, instrument, pnl = pos_match.groups()
                    
                    self.position_closes.append(PositionClose(
                        timestamp=timestamp,
                        instrument=instrument.replace('-SPOT.BYBIT', ''),
                        realized_pnl=Decimal(pnl),
                    ))
    
    def calculate_pnl_summary(self) -> Dict:
        """Calculate comprehensive P&L summary"""
        
        # Group by instrument
        by_instrument = defaultdict(lambda: {
            'fills': 0,
            'buy_volume': Decimal('0'),
            'sell_volume': Decimal('0'),
            'total_fees': Decimal('0'),
            'realized_pnl': Decimal('0'),
        })
        
        # Process fills
        for fill in self.fills:
            stats = by_instrument[fill.instrument]
            stats['fills'] += 1
            stats['total_fees'] += fill.commission_usdt
            
            if fill.side == 'BUY':
                stats['buy_volume'] += fill.notional_value
            else:
                stats['sell_volume'] += fill.notional_value
        
        # Process position closes (realized P&L)
        for close in self.position_closes:
            by_instrument[close.instrument]['realized_pnl'] += close.realized_pnl
        
        # Calculate totals
        total_fills = len(self.fills)
        total_fees = sum(s['total_fees'] for s in by_instrument.values())
        total_realized_pnl = sum(s['realized_pnl'] for s in by_instrument.values())
        total_volume = sum(s['buy_volume'] + s['sell_volume'] for s in by_instrument.values())
        
        # Net P&L = Realized P&L - Fees
        net_pnl = total_realized_pnl - total_fees
        
        return {
            'by_instrument': dict(by_instrument),
            'total_fills': total_fills,
            'total_fees': total_fees,
            'total_realized_pnl': total_realized_pnl,
            'net_pnl': net_pnl,
            'total_volume': total_volume,
            'earliest_fill': self.fills[0].timestamp if self.fills else None,
            'latest_fill': self.fills[-1].timestamp if self.fills else None,
        }
    
    def print_summary(self):
        """Print formatted P&L summary"""
        
        summary = self.calculate_pnl_summary()
        
        print("=" * 70)
        print("SPOT TRADING P&L SUMMARY")
        print("=" * 70)
        print()
        
        if summary['earliest_fill'] and summary['latest_fill']:
            print(f"Period: {summary['earliest_fill']} to {summary['latest_fill']}")
            print()
        
        print(f"Total Fills:          {summary['total_fills']:>10,}")
        print(f"Total Volume:         {summary['total_volume']:>10,.2f} USDT")
        print()
        
        print("-" * 70)
        print("BY INSTRUMENT")
        print("-" * 70)
        print(f"{'Instrument':<12} {'Fills':>8} {'Volume':>14} {'Fees':>12} {'Real. P&L':>12} {'Net':>12}")
        print("-" * 70)
        
        for instrument in sorted(summary['by_instrument'].keys()):
            stats = summary['by_instrument'][instrument]
            total_vol = stats['buy_volume'] + stats['sell_volume']
            net = stats['realized_pnl'] - stats['total_fees']
            
            print(f"{instrument:<12} "
                  f"{stats['fills']:>8,} "
                  f"{total_vol:>14,.2f} "
                  f"{stats['total_fees']:>12,.4f} "
                  f"{stats['realized_pnl']:>12,.4f} "
                  f"{net:>12,.4f}")
        
        print("-" * 70)
        print(f"{'TOTAL':<12} "
              f"{summary['total_fills']:>8,} "
              f"{summary['total_volume']:>14,.2f} "
              f"{summary['total_fees']:>12,.4f} "
              f"{summary['total_realized_pnl']:>12,.4f} "
              f"{summary['net_pnl']:>12,.4f}")
        print("=" * 70)
        print()
        
        # P&L Analysis
        print("P&L BREAKDOWN")
        print("-" * 70)
        print(f"Realized P&L (from closed positions): {summary['total_realized_pnl']:>12,.4f} USDT")
        print(f"Total Fees Paid:                      {summary['total_fees']:>12,.4f} USDT")
        print(f"{'─' * 52}")
        print(f"NET P&L (Spot Trades Only):           {summary['net_pnl']:>12,.4f} USDT")
        print("=" * 70)
        print()
        
        # Fee analysis
        if summary['total_volume'] > 0:
            avg_fee_bps = (summary['total_fees'] / summary['total_volume']) * 10000
            print(f"Average Fee Rate: {avg_fee_bps:.2f} bps")
            print()


def main():
    parser = argparse.ArgumentParser(
        description='Analyze spot trading P&L from NautilusTrader logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        '--log-file',
        type=str,
        default='/home/ubuntu/trading/logs/vip_sui_heavy_live.log',
        help='Path to log file (default: VPS live log)',
    )
    
    parser.add_argument(
        '--since',
        type=str,
        help='Only analyze trades since timestamp (format: 2026-02-04T15:10:00.000000000Z or 2026-02-04T15:10)',
    )
    
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local log directory instead of VPS path',
    )
    
    args = parser.parse_args()
    
    # Adjust log path if local
    if args.local:
        log_path = Path(__file__).parent.parent / 'logs' / 'vip_sui_heavy.service.log'
    else:
        log_path = Path(args.log_file)
    
    if not log_path.exists():
        print(f"Error: Log file not found: {log_path}")
        print()
        print("Tip: Use --local flag if running on development machine")
        return 1
    
    # Normalize the --since timestamp to match log format
    since = None
    if args.since:
        # Normalize to full timestamp format for comparison
        since = args.since
        # If short format, pad to full timestamp
        if 'T' in since and len(since) < 24:
            # Pad to microseconds and add Z
            parts = since.split('T')
            if len(parts) == 2:
                date_part, time_part = parts
                # Ensure time has seconds and microseconds
                time_components = time_part.split(':')
                if len(time_components) == 2:
                    time_part += ':00'
                if '.' not in time_part:
                    time_part += '.000000000'
                if not time_part.endswith('Z'):
                    time_part += 'Z'
                since = f"{date_part}T{time_part}"
    
    # Parse logs
    analyzer = SpotPnLAnalyzer()
    print(f"Parsing log file: {log_path}")
    if since:
        print(f"Filtering trades since: {since}")
    print()
    
    analyzer.parse_log_file(log_path, since=since)
    
    if not analyzer.fills and not analyzer.position_closes:
        print("No trading activity found in logs")
        return 0
    
    # Print summary
    analyzer.print_summary()
    
    return 0


if __name__ == '__main__':
    exit(main())
