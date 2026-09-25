# Telegram bots for picker and courier

Two Telegram bots for a delivery service. Both bots use one SQLite database.

## Features

- employee registration and administrator approval;
- picker order assembly;
- courier pickup, delivery, and rejection with a reason;
- order history and administrator feed;
- timeout alerts;
- employee statistics and shift reports;
- employee shift status and administrator access control.

## Setup

1. Create a virtual environment.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in the real values:

```powershell
Copy-Item .env.example .env
```

4. Initialize the database and optionally create a test order:

```powershell
python create_test_order.py
```

## Run

Run the picker bot in one terminal:

```powershell
python bot.py
```

Run the courier bot in another terminal:

```powershell
python courier_bot.py
```

The database file `delivery.db` is created locally and is intentionally ignored by Git.

## Administrator commands

- `/applications` - review employee applications;
- `/employees` - enable or disable approved employees;
- `/orders` - view the order feed;
- `/stats` - view employee statistics;
- `/timeouts` - view overdue orders.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Never commit `.env`, bot tokens, `delivery.db`, or virtual environments.
