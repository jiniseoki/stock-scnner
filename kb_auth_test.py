importe os
import sys
import requests

URL = "https://developer.kbsec.com:32484/oauth2/token"

def main():
    key = os.getenv("KB_APP_KEY")
    secret = os.getenv("KB_APP_SECRET")

    if not key or not secret:
        print("FAIL: MISSING_ENV")
        return 1

    payload = {
        "grant_type": "client_credentials",
        "appKey": key,
        "appSecret": secret
    }

    try:
        response = requests.post(
            URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=20
        )

        if response.status_code != 200:
            print("FAIL: HTTP", response.status_code)
            return 1

        data = response.json()

        if not data.get("access_token"):
            print("FAIL: TOKEN_MISSING")
            return 1

        print("SUCCESS: KB_OAUTH_CONNECTED")
        print("EXPIRES_IN:", data.get("expires_in"))
        return 0

    except requests.exceptions.Timeout:
        print("FAIL: TIMEOUT")
    except requests.exceptions.RequestException:
        print("FAIL: NETWORK_ERROR")
    except ValueError:
        print("FAIL: INVALID_JSON")

    return 1

if __name__ == "__main__":
    sys.exit(main())
