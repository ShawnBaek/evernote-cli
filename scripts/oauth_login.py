"""Run the evernote-backup OAuth flow without a TTY.

Opens the auth URL in the user's browser, listens on localhost:10500
for the callback, prints the resulting auth token to stdout (last line).
"""
from __future__ import annotations

import sys

from evernote_backup.cli_app_auth_oauth import get_oauth_client
from evernote_backup.evernote_client_oauth import (
    EvernoteOAuthCallbackHandler,
    OAuthDeclinedError,
)


def main() -> int:
    client = get_oauth_client(backend="evernote", custom_api_data=None)
    handler = EvernoteOAuthCallbackHandler(client, oauth_port=10500, server_host="localhost")
    url = handler.get_oauth_url()
    print(f"OAUTH_URL: {url}", flush=True)

    # Open in browser (macOS-friendly).
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass

    print("Waiting for browser approval...", flush=True)
    try:
        token = handler.wait_for_token()
    except OAuthDeclinedError:
        print("DECLINED", flush=True)
        return 2
    print(f"TOKEN: {token}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
