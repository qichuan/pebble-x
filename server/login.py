"""One-off local login for Peep. NOT deployed to Vercel.

Run on your own machine (your home IP, which X trusts more than a datacenter):

    cd server
    pip install -r requirements.txt
    python login.py

It logs in to X with twikit, then prints:
  - X_COOKIES : paste as a Vercel environment variable
  - APP_TOKEN : a fresh shared secret for the watch <-> server auth

Your password is only used for this login and is never stored or printed.
"""
import asyncio
import getpass
import json
import secrets

from twikit import Client


async def main():
    print("X login (nothing is stored; used once to mint a session cookie)\n")
    username = input("X username (without @): ").strip()
    email = input("X email: ").strip()
    password = getpass.getpass("X password: ")

    client = Client("en-US")
    await client.login(auth_info_1=username, auth_info_2=email, password=password)

    cookies = client.get_cookies()
    print("\n" + "=" * 60)
    print("Set these as environment variables in your Vercel project:")
    print("=" * 60)
    print("\nX_COOKIES=" + json.dumps(cookies))
    print("\nAPP_TOKEN=" + secrets.token_urlsafe(24))
    print("\n(Use the same APP_TOKEN value in the Pebble app settings page.)")


if __name__ == "__main__":
    asyncio.run(main())
