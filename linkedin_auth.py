#!/usr/bin/env python3
"""
LinkedIn Auth Setup — uses LinkedIn's built-in OAuth token generator.
No callback server needed. Run once, saves token to .env
"""

import os
import webbrowser
from pathlib import Path

import requests
from dotenv import set_key

ENV_PATH = Path(__file__).parent / ".env"
ENV_PATH.touch(exist_ok=True)


def main():
    print("\n\033[1m\033[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
    print("\033[1m\033[36m  LinkedIn Auth Setup\033[0m")
    print("\033[1m\033[36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n")

    print("Opening LinkedIn's token generator in your browser…\n")
    webbrowser.open("https://www.linkedin.com/developers/tools/oauth/token-generator")

    print("In the browser:")
    print("  1. Select your app  (\033[1mcontent auth\033[0m)")
    print("  2. Check these scopes:  \033[1mw_member_social\033[0m  \033[1mopenid\033[0m  \033[1mprofile\033[0m")
    print("  3. Click \033[1mRequest access token\033[0m")
    print("  4. Authorize if prompted")
    print("  5. Copy the token that appears\n")
    print("\033[33m  Note: if openid/profile scopes are missing, go to your app →\033[0m")
    print("\033[33m  Products → add 'Sign In with LinkedIn using OpenID Connect'\033[0m\n")

    try:
        token = input("\033[1mPaste your access token here:\033[0m  ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    if not token:
        print("\033[31mNo token entered.\033[0m")
        return

    # Get person URN via token introspection (no profile scope needed)
    print("\nVerifying token…")
    from dotenv import dotenv_values
    creds = dotenv_values(ENV_PATH)
    client_id = creds.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = creds.get("LINKEDIN_CLIENT_SECRET", "").strip()

    member_id = None
    name = "LinkedIn User"

    if client_id and client_secret:
        intro = requests.post(
            "https://www.linkedin.com/oauth/v2/introspectToken",
            data={
                "token": token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if intro.status_code == 200:
            data = intro.json()
            if not data.get("active", False):
                print("\033[31mToken is expired or invalid. Generate a new one.\033[0m")
                return
            member_id = data.get("sub") or data.get("member_id")

    # Fallback: ask user for member ID
    if not member_id:
        print("\033[33mCouldn't auto-detect your LinkedIn member ID.\033[0m")
        print("Find it at: linkedin.com/in/yourprofile → view page source → search 'memberId'")
        print("Or check the token generator page after generating the token.\n")
        member_id = input("\033[1mPaste your LinkedIn member ID (numbers only):\033[0m  ").strip()
        if not member_id:
            print("\033[31mNo member ID provided.\033[0m")
            return

    # Try to resolve the real person URN from /v2/userinfo (works with openid+profile)
    userinfo_resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if userinfo_resp.status_code == 200:
        sub = userinfo_resp.json().get("sub", "").strip()
        if sub:
            person_urn = f"urn:li:person:{sub}"
        else:
            person_urn = f"urn:li:member:{member_id}"
    else:
        person_urn = f"urn:li:member:{member_id}"

    # Save to .env
    set_key(str(ENV_PATH), "LINKEDIN_ACCESS_TOKEN", token)
    set_key(str(ENV_PATH), "LINKEDIN_PERSON_URN", person_urn)

    print(f"\n\033[1m\033[32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m")
    print(f"\033[1m\033[32m  Authorized as: {name}\033[0m")
    print(f"\033[32m  URN: {person_urn}\033[0m")
    print(f"\033[32m  Token saved to .env\033[0m")
    print(f"\033[1m\033[32m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n")
    print("You're set. Run the pipeline and it'll ask before posting.\n")


if __name__ == "__main__":
    main()
