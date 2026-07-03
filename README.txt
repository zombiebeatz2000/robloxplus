1. Install dependencies:
   py -m pip install -r requirements.txt
   py -m playwright install chromium

2. Log in once:
   py main.py --login

3. Check a user:
   py main.py username

If the saved Roblox session expires, the browser opens again automatically.
The .ROBLOSECURITY value is stored in .env and is never printed.
