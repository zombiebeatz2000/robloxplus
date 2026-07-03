# Roblox Plus Checker

Check whether a Roblox user has the Roblox Plus badge.

The tool logs into Roblox once through Playwright, stores the session cookie locally, and queries Roblox profile data by username.

## Features

- Checks Roblox Plus status
- Resolves usernames to user IDs
- Saves the Roblox session locally
- Automatically reopens login when the session expires
- Never prints the `.ROBLOSECURITY` cookie

## Installation

```powershell
py -m pip install -r requirements.txt
py -m playwright install chromium
```

```Login once
py main.py --login
```

py main.py username
```usage
py main.py username
```

## Example:

- user: @builderman
- userid: 156
- roblox plus: yes
 raw plus field: true
- HTTPS status: 200
