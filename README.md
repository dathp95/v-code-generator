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
The pure helper API remains storage-independent; application entry points use
`services/issuance_service.py` to persist keys before delivery.

```text
services/issuance_service.py           # Transactional request approval and issuance
storage/database.py                    # Connections, foreign keys, schema versioning
storage/migrations/001_initial.sql      # Transactional initial schema
storage/repositories/request_repository.py
storage/repositories/license_repository.py
storage/repositories/delivery_repository.py
```

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

The CLI and Telegram enforce the same 1–3650-day service boundary. Both record
manual issuance before displaying or sending a key. Every new CLI invocation or
`/generate` command is a new manual issuance; use the stored license for recovery
rather than repeating an issuance command after uncertain output.

## SQLite configuration and state

The default database is `data/license_manager.sqlite3` relative to this repository,
regardless of the process's working directory. Override it with `LICENSE_DB_PATH`
in the environment (or the bot's local `.env`). Relative overrides also resolve
against the repository root. For example, before launching either entry point:

```powershell
$env:LICENSE_DB_PATH = "C:\REDO\LicenseGenerator\data\license_manager.sqlite3"
```

Startup initializes the schema atomically using versioned SQL migrations and
`PRAGMA user_version`. Repeated initialization is safe; a schema newer than the
application is refused. Foreign keys are enabled on every connection. `sqlite3`
is included with Python; no database server is required.

The database keeps requests (channel, recipient/user IDs, customer, normalized
Device ID, approval status, UTC timestamps), licenses (one per request, exact signed
token and payload, edition, duration, UTC dates, issuer/source), and separate
delivery attempts (purpose, destination, attempt number, outcome, safe error
category, timestamps, Telegram message ID when acknowledged). An approved request
can have a license whose delivery failed. A successful delivery means Telegram
acknowledged the message; it does **not** mean the customer activated V-CODE.

Private keys, passphrases, and bot credentials are never stored in SQLite.
The database does contain usable activation tokens and customer information;
restrict access to the admin account and protect its backups. `data/`, SQLite
databases and sidecars are ignored by Git. Database failures stop key delivery.

## Manual retries and upgrade limitations

Customers resubmitting the same channel/user/Device ID reuse an open request.
Requests are saved before notifying Admin. Failed admin notifications have their
own attempts; submitting the Device ID again retries that notification without
creating a duplicate request. A successful notification is not resent on every
submission.

Only the administrator in a private chat can approve and issue customer keys.
Selecting a request and entering its duration saves its license before delivery.
After a failed delivery, enter the same duration again. After restart, click the
original `request:<id>` button and enter that duration. The exact stored token,
payload, license ID, and recipient are reused. Repeated approval cannot create a
second license for the same request. Already acknowledged customer deliveries
are not resent through the request flow.

For keys created manually with `/generate` or the admin menu, use the displayed
License ID with `/retry LICENSE_ID` to resend the same stored key to Admin,
including after restart. This command is admin-only. It does not create a license.
CLI licenses and output-attempt records can be recovered from the protected DB;
there is no CLI retry command in this upgrade.

Network failures, timeouts, and rate limits are recorded as retryable. Forbidden,
invalid-chat/bad-request, and other Telegram failures are recorded separately as
permanent failures; fix the delivery problem before retrying manually. There is
no background retry worker. Expired stored keys are refused, never silently
reissued: ask the customer to submit their Device ID again to create a new request
and obtain fresh admin approval.

Timeouts or crashes can happen after Telegram accepts a message but before its
acknowledgment is saved. An `attempting` record left after a crash has an unknown
outcome. A manual retry can therefore produce a duplicate message with the same
key; exactly-once Telegram delivery is **not guaranteed**. A DB failure while
recording acknowledgment also leaves the outcome uncertain.

Only transient input forms remain in RAM. New request buttons work after restart
against the same database. Pre-upgrade RAM-only requests cannot be recovered;
old buttons show a resubmission message. Previously generated output files are
not imported. Keep this database when upgrading or moving the bot; starting with
an empty DB loses deduplication and retry history.

## Backup and restore

Do not copy a live SQLite database file by itself. Use SQLite's backup API, or
stop **all** bot/CLI processes accessing the DB before an offline backup. From the
repository, this creates a consistent online backup (choose a new destination
file in an existing, protected backup directory):

```powershell
@'
import sqlite3
from contextlib import closing
from storage.database import Database

with closing(sqlite3.connect(Database().path.as_uri() + "?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(r"C:\Secure\Backups\license_manager-backup.sqlite3")) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
'@ | python -
```

The CLI reads `LICENSE_DB_PATH` from the environment, so set it explicitly for
backup if the bot obtains its override from `.env`. Back up private keys separately
using the existing secure key-management process.

To restore, stop all processes using the database. Preserve a consistent backup
of the current DB first. Restore into a fresh directory to avoid stale journal,
WAL, or SHM files: open the backup read-only with `sqlite3.connect(..., uri=True)`
and call `backup()` into a new destination connection, as above. Check
`PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_key_check` returns no rows.
Set `LICENSE_DB_PATH` to that restored file and restart the bot. Retain the original
database and backup until restoration is verified. A restored older backup may
lack later acknowledgments, so retry messages cautiously; it may also lack later
issued licenses. Never restore while the service is running.

Run the offline tests with temporary keys and mocked Telegram delivery:

```powershell
python -m unittest discover -s tests -v
```

## Legacy license.lic flow

`generate_license.py` is deprecated and disabled by default. It is retained only for historical reference. New customers must receive Activation Keys from `generate_activation.py`.
