"""Universal Scanner API (V6.2.0)."""

from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.scanner.providers import CryptoScannerProvider, StaticScannerProvider
from bot.terminal.scanner.ranking import compute_score
from bot.terminal.scanner.registry import ScannerRegistry, get_scanner_registry
from bot.terminal.scanner.scanner import ScannerService, get_scanner_service

__all__ = [
    "CryptoScannerProvider",
    "RankComponents",
    "ScannerRegistry",
    "ScannerResult",
    "ScannerService",
    "StaticScannerProvider",
    "compute_score",
    "get_scanner_registry",
    "get_scanner_service",
]
