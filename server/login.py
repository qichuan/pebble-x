"""One-off local setup for TweetFit. NOT deployed to Vercel.

Legacy fallback — prefer the /setup wizard on your deployed server, which
stores the cookies without touching env vars or redeploying.

X now blocks automated username/password login behind Cloudflare, so instead of
logging in programmatically we reuse the session from a browser where you're
already logged in to x.com. Your browser passed Cloudflare normally, so its
session cookies work for API calls.

    cd server
    pip install -r requirements.txt
    python login.py

It asks for two cookies from your logged-in x.com browser tab, then prints:
  - X_COOKIES : paste as a Vercel environment variable
  - APP_TOKEN : a fresh shared secret for the watch <-> server auth

How to get the two cookies (do this in a browser where x.com is logged in):
  1. Open x.com, then open DevTools (F12 / Cmd-Opt-I).
  2. Application (Chrome) or Storage (Firefox) tab -> Cookies -> https://x.com
  3. Copy the *Value* of the cookie named `auth_token`  (a long hex string).
  4. Copy the *Value* of the cookie named `ct0`         (the CSRF token).
Treat auth_token like a password — it grants access to your X account.
"""
import getpass
import json
import secrets


def main():
    print("TweetFit setup — reuse your browser's x.com session (no password needed)\n")
    auth_token = getpass.getpass("x.com cookie 'auth_token': ").strip()
    ct0 = input("x.com cookie 'ct0': ").strip()

    if not auth_token or not ct0:
        print("\nBoth auth_token and ct0 are required. Aborting.")
        return

    cookies = {"auth_token": auth_token, "ct0": ct0}

    print("\n" + "=" * 60)
    print("Set these as environment variables in your Vercel project:")
    print("=" * 60)
    print("\nX_COOKIES=" + json.dumps(cookies))
    print("\nAPP_TOKEN=" + secrets.token_urlsafe(24))
    print("\n(Use the same APP_TOKEN value in the Pebble app settings page.)")
    print("\nVerify after deploy:  curl -H 'Authorization: Bearer <APP_TOKEN>' \\")
    print("                        <your-url>/api/timeline?feed=following")


if __name__ == "__main__":
    main()
