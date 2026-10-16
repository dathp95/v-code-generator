# v-code-generator

Admin-only tooling for issuing V-CODE machine-bound Activation Keys.

V-CODE validates Activation Keys in `UDS-Analysis/license/activation.py`. Do not change the token format here unless the V-CODE validator changes in the same way.

## Security rules

- Treat the old committed `private.pem` as compromised.
- Generate a fresh RSA 3072-bit key pair before issuing real Activation Keys.
- Store production private keys outside this repository.
- Keep private keys encrypted with a strong passphrase.
- Never commit or package private keys.
- Never print private key material in logs, tickets, or chat.
- Do not automatically regenerate or overwrite production keys.

## Generate a key pair

Use a secure admin directory outside the repository:

```powershell
python generate_keys.py --output-dir C:\Secure\VCodeKeys
```

The script prompts for a passphrase without echoing it and writes:

- `vcode-activation-private.pem`
- `vcode-activation-public.pem`

If files already exist, the script refuses to overwrite them unless you pass `--force` and type `YES` at the confirmation prompt.

## Synchronize the public key with V-CODE

1. Open the generated `vcode-activation-public.pem`.
2. Copy only the public PEM text.
3. In the V-CODE repository, update `UDS-Analysis/license/trusted_public_key.py` so `TRUSTED_PUBLIC_KEY_PEM` contains the new public key bytes.
4. Run the V-CODE license tests and confirm activation succeeds with a temporary test key.
5. Keep the encrypted private key offline in the admin key store.

## Verify a key pair

Before issuing Activation Keys, confirm the private key matches the public key embedded into V-CODE:

```powershell
python verify_key_pair.py --private-key C:\Secure\VCodeKeys\vcode-activation-private.pem --public-key C:\Secure\VCodeKeys\vcode-activation-public.pem
```

Expected output is `MATCH`.

## Generate a 30-day Activation Key

Get the customer's Device ID from V-CODE: License Support -> Product Activation.

```powershell
python generate_activation.py --device-id VC-1234-5678-ABCD-EF12-3456 --customer "Customer A" --edition Professional --days 30 --private-key C:\Secure\VCodeKeys\vcode-activation-private.pem
```

The script prints a text Activation Key in this format:

```text
base64url(payload).base64url(signature)
```

Optionally save the Activation Key to an ignored file:

```powershell
python generate_activation.py --device-id VC-1234-5678-ABCD-EF12-3456 --customer "Customer A" --edition Professional --days 30 --private-key C:\Secure\VCodeKeys\vcode-activation-private.pem --output-file output\activation-customer-a.txt
```

## Test in V-CODE

1. Build or run V-CODE with the matching public key in `license/trusted_public_key.py`.
2. Open License Support -> Product Activation.
3. Confirm the displayed Device ID matches the Device ID used by this generator.
4. Paste the Activation Key.
5. Activation should save `activation.dat` and report the customer, edition, and expiry date.

For an automated compatibility check against a local V-CODE checkout, set `VCODE_REPO_PATH` before running tests:

```powershell
$env:VCODE_REPO_PATH = "C:\path\to\UDS-Analysis"
python -m unittest discover -s tests
```

Without `VCODE_REPO_PATH`, the tests still verify the same token contract locally without requiring production keys.

## Shared license generation

The shared core has no Telegram, GUI, HTTP, or storage dependencies:

```text
core/
  exceptions.py                 # Validation and safe key-loading errors
  models/issued_activation.py   # IssuedActivation: activation_key and payload
  crypto/activation.py          # Payload validation, canonical JSON, Base64URL, RSA signing
  crypto/key_loader.py          # Encrypted RSA private-key loading
  services/license_service.py   # LicenseService(private_key).issue_activation(...)
```

`LicenseService.issue_activation(device_id, customer, edition, days, issued_at=None)`
accepts 1–3650 whole days and returns an `IssuedActivation`. Tokens retain the
version 1 schema, UTC timestamps, and RSA-PSS-SHA256 signature settings.
Existing helper imports from `generate_activation.py` remain supported.

## Launch the CLI or Telegram bot

Use Python 3.11 or later and install the dependencies:

```powershell
python -m pip install cryptography python-telegram-bot python-dotenv
python generate_activation.py --device-id VC-1234-5678-ABCD-EF12-3456 --customer "Customer A" --edition Professional --days 30 --private-key C:\Secure\VCodeKeys\vcode-activation-private.pem
python telegram_bot.py
```

The CLI prompts with `getpass`, displays local issue/expiry times, and supports
`--output-file` as shown above. Keep generated files under the ignored `output/`
directory.

Configure the Telegram bot through environment variables or a local, ignored
`.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_ID`, `VC_PRIVATE_KEY`, and
`VC_KEY_PASSWORD`. Keep the encrypted private key outside the repository.
The bot loads one `LicenseService` at startup. Customers submit their Device ID;
only the administrator can approve requests and issue keys, use
`/generate DEVICE_ID DAYS`, or generate manually from the admin menu.

Requests, recipient mappings, and issued keys awaiting delivery are stored only
in RAM. A failed delivery retries the same cached key and marks the request sent
only after successful delivery. Restarting the bot loses these records; customers
must submit their requests again. There is no persistent request history.

Run the offline tests with temporary keys and mocked Telegram delivery:

```powershell
python -m unittest discover -s tests -v
```

## Legacy license.lic flow

`generate_license.py` is deprecated and disabled by default. It is retained only for historical reference. New customers must receive Activation Keys from `generate_activation.py`.
