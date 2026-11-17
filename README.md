# Logsti Telegram Logistics Bot

A first working version of a Telegram trip and passenger-payment tracker.

## Features
- Start a trip with starting odometer, passenger count, and standard fare.
- Automatically creates a fare record for each passenger.
- Change an individual passenger's fare.
- Record partial or full payments and payment method.
- Record fuel and other trip expenses.
- View trip status and unpaid passenger balances.
- Close trips and view daily/monthly summaries.
- Stores records in SQLite.

## Setup
1. Create a Telegram bot with BotFather and copy its bot token.
2. Deploy this project as a worker on Railway (or another always-on Python host).
3. Set the environment variable TELEGRAM_BOT_TOKEN to the bot token in the hosting dashboard.
4. Keep DB_PATH at its default unless you have configured a persistent disk. Attach persistent storage for logistics_bot.db so records survive redeployments.
5. Start the worker with the Procfile command: python main.py.

## Telegram commands
- /newtrip 12842 16 100
- /paid 7 600 Telebirr
- /fare 7 150
- /expense 1500 Fuel
- /status
- /close
- /today
- /month
- /help

## Important notes
- Payments are recorded as NOT VERIFIED. Confirm them in Telebirr or your bank app yourself.
- This version does not yet read voice notes or connect to a Telebirr transaction API.
- The bot uses Africa/Addis_Ababa local time for reports.
- Do not put your bot token, Composio token, or banking credentials in GitHub.
