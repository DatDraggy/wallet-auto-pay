# Wallet Auto-Pay for Home Assistant

Replicate a "Debit Card" experience for credit cards.

## Features
- **Silent Monitoring**: Listens for Google Wallet notifications from the HA Companion App.
- **Actionable Push**: Asks "Pay Now" or "Add to To-Do List" immediately after a tap-to-pay event.
- **FinTS Integration**: Executes a secure SEPA transfer from your checking account to your credit card using the `python-fints` library.
- **Multi-User**: Supports multiple independent bank accounts and phone sensors via Config Flow.

## Setup
1. Install this integration via HACS.
2. Ensure the Home Assistant Companion App has the "Last Notification" sensor enabled. (With restrictions to monitor Google Pay only!)
3. Add the integration via the Home Assistant UI and follow the setup wizard.

## Disclaimer
This software is not affiliated with any bank. Use it at your own risk. Always verify your transactions.
