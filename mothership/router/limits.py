"""
Per-ship-class concurrency limits.
Imported by dispatcher.py and exposed here for easy tuning.
"""
from config import MAX_COLLECTORS, MAX_RESEARCHERS, MAX_DESTROYERS

SHIP_LIMITS = {
    "collector":  MAX_COLLECTORS,
    "researcher": MAX_RESEARCHERS,
    "destroyer":  MAX_DESTROYERS,
    "scout":      8,
    "support":    4,
}
