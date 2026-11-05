# Example bot

`echo.py` runs in your own Python process and reads only its bot token from
protected process configuration. It demonstrates token-only startup, commands,
callbacks and acknowledged polling; no credentials or account data are included.

With SDK 0.0.1.dev2, `Bot(token)` uses `https://api.vlookme.com` automatically;
no host or IP setting is needed for the primary VLook service.
