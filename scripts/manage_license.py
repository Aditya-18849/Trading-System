"""
Vendor License & Payment Kill-Switch Management Utility
======================================================
Allows the software vendor/admin to manually lock or unlock the trading workstation
if the client has overdue payments or subscription defaults.

GUARANTEE:
- Locking the system NEVER deletes, resets, or modifies any client trade logs,
  database entries, API keys, or historical reports.
- Access is gracefully restored the moment `unlock` is executed.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

LOCK_FILE = Path(".license_lock")


def lock_system(reason: str = "Overdue subscription payment"):
    """Activate the kill-switch. Blocks all trading and API requests with HTTP 402."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(f"LOCKED_AT: {timestamp}\nREASON: {reason}\n")

    print("=" * 70)
    print("[KILL-SWITCH ACTIVATED] Trading System is now SUSPENDED.")
    print(f"Timestamp: {timestamp}")
    print(f"Reason:    {reason}")
    print("All client data, trade history, and databases remain 100% PRESERVED.")
    print("To restore client access upon payment, run:")
    print("   python scripts/manage_license.py unlock")
    print("=" * 70)


def unlock_system():
    """Deactivate the kill-switch. Restores full workstation access."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
        print("=" * 70)
        print("[KILL-SWITCH DEACTIVATED] Workstation access RESTORED.")
        print("All features and APIs are now operational with all client data intact.")
        print("=" * 70)
    else:
        print("[INFO] System is already active and unlocked.")


def check_status():
    """Check the current lock status."""
    if LOCK_FILE.exists():
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            details = f.read().strip()
        print("Status: [LOCKED / SUSPENDED]")
        print(details)
    else:
        print("Status: [ACTIVE / UNLOCKED] (Normal Operation)")


def main():
    parser = argparse.ArgumentParser(description="Vendor License & Payment Kill-Switch")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Lock command
    lock_parser = subparsers.add_parser("lock", help="Suspend client workstation access")
    lock_parser.add_argument("--reason", default="Overdue subscription payment", help="Reason for suspension")

    # Unlock command
    subparsers.add_parser("unlock", help="Restore client workstation access")

    # Status command
    subparsers.add_parser("status", help="Check current license status")

    args = parser.parse_args()

    if args.command == "lock":
        lock_system(args.reason)
    elif args.command == "unlock":
        unlock_system()
    elif args.command == "status":
        check_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
