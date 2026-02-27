import argparse
import json
import os

from google_auth_oauthlib.flow import InstalledAppFlow

from agent.token_store import upsert_token
from agent.tools import SCOPES


def _sanitize_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value.strip())


def _write_payload(output_dir: str, label: str, email: str, token_json: dict) -> str:
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"token_output_{_sanitize_filename(label)}.json")
    payload = {
        "email": email,
        "label": label,
        "token_json": token_json,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Connect a Gmail inbox via OAuth and generate a token payload file for POST /api/accounts.\n"
            "By default this writes only to token_outputs/; it does NOT modify the API's live token store in tokens/.\n"
            "The API starts using the token only after you POST the payload to /api/accounts."
        )
    )
    parser.add_argument("--credentials", default="credentials.json", help="Path to shared OAuth client credentials JSON")
    parser.add_argument("--email", default="", help="Inbox email address")
    parser.add_argument("--label", default="", help="Code name/label for this inbox (used in token_output filename)")
    parser.add_argument("--output-dir", default="token_outputs", help="Directory to store token_output_*.json payloads")
    parser.add_argument(
        "--register-local",
        action="store_true",
        help="Also save this token into tokens/ (local testing only). Production should register via /api/accounts.",
    )
    args = parser.parse_args()

    # if not os.path.exists(args.credentials):
    #     raise SystemExit(f"Missing credentials file: {args.credentials}")

    # Interactive prompt mode (matches token_generator workflow)
    if not args.email:
        ans = input("Credentials are in JSON format. Do you want to generate a token? (Y/N): ").strip().lower()
        if ans not in ("y", "yes"):
            print("Exiting.")
            return 0
        args.email = input("Enter Gmail ID (email): ").strip()
        args.label = input("Enter a code name for the token: ").strip()

    if not args.email:
        raise SystemExit("email is required")
    if not args.label:
        raise SystemExit("code name (label) is required")

    flow = InstalledAppFlow.from_client_secrets_file(args.credentials, SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    token_json = json.loads(creds.to_json())
    if not token_json.get("refresh_token"):
        raise SystemExit(
            "No refresh_token returned. Try revoking access for this app in Google Account settings, "
            "then run again (prompt=consent is already requested)."
        )

    payload_path = _write_payload(args.output_dir, args.label, args.email, token_json)
    print("\n=== Token payload generated ===")
    print(f"Email: {args.email}")
    print(f"Label: {args.label}")
    print(f"Saved payload file (safe to share internally): {payload_path}")
    print("\nNext step (REQUIRED): register with the running API (this creates/updates tokens/token_*.json):")
    print(
        f'  Invoke-RestMethod -Uri "http://127.0.0.1:2002/api/accounts" -Method POST -ContentType "application/json" -Body (Get-Content -Raw "{payload_path}")'
    )
    print("\nAfter registering, verify via:")
    print('  Invoke-RestMethod -Uri "http://127.0.0.1:2002/api/accounts_health" -Method GET | ConvertTo-Json -Depth 6')

    if args.register_local:
        token_file = upsert_token(email=args.email, token_json=token_json, label=args.label)
        print("\n(Local) Also wrote directly into tokens/ (bypasses /api/accounts):")
        print(f"  tokens/{token_file} (email={args.email})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
