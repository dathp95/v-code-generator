# v-code-generator

Admin-only tooling for issuing V-CODE machine-bound Activation Keys.

The local PySide6 desktop provides request/license browsing, delivery history, and
confirmed manual issuance. CLI and Telegram entry points remain independent.

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
services/approval_service.py           # Expiring, single-use confirmation forms
services/delivery_service.py           # Claimed delivery, deadlines, safe error handling
storage/database.py                    # Connections, foreign keys, schema versioning
storage/migrations/001_initial.sql      # Transactional initial schema
storage/migrations/002_reliable_workflow.sql # Claims, unknown outcomes, rejection audit, forms
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

## Launch the desktop

Use Python 3.11 or later; the desktop dependency set in `requirements-gui.txt`
was tested with Python 3.12.10, PySide6 6.8.3, cryptography 49.0.0, and SQLite
3.49.1 with JSON support. From this repository, install and launch:

```powershell
python -m pip install -r requirements-gui.txt
python main.py
```

No Telegram token or private key is needed to open the desktop. Importing the GUI
does not import bot configuration or start polling. `generate_activation.py` and
`telegram_bot.py` remain separately runnable; this version has no bot Start/Stop
controls.

The desktop uses the same repository-relative `data/license_manager.sqlite3` and
`LICENSE_DB_PATH` resolution as the CLI/bot. Set the environment override before
launching each process if you use another DB. The desktop deliberately does not
load `.env`: if the bot gets its DB override from `.env`, set that same path in
the desktop's environment explicitly. Use Settings to verify the active path.
All processes must share a local SQLite file with appropriate permissions. Existing
schema migrations are reused without a new migration or token-format changes.

- **Requests** is read-only, with customer/Device ID/request ID search, status
  filtering, 25-row pages, sortable columns, and selected-request details. Approval
  and rejection remain in Telegram. Rejected requests are distinguished from
  other closed requests.
- **Licenses** shows metadata and linked requests, never full tokens in cells or
  details. Select a license and explicitly use **Copy Key** to read it into the
  clipboard, even while signing is locked. **Issue manually** opens a form for
  customer, Device ID, edition, and 1–3650 days. Choose **Review**, then **Confirm
  issuance**. Cancel/close before confirmation creates nothing. A confirmed action
  saves through `IssuanceService` with source `desktop_manual` and issuer
  `local_admin` before enabling Copy Key. Repeated clicks do not issue again. No
  Telegram delivery or delivery-history attempt is created by desktop issuance.
- **Delivery History** is read-only. Unknown outcomes are highlighted and labeled
  for admin review; the desktop cannot resend. Sent means Telegram acknowledged
  delivery, not that V-CODE was activated. History shows stored attempt/lease
  states; lease recovery and retry decisions remain in the bot.
- **Settings** shows the database path and system display timezone, and saves
  Light/Dark preferences in the ignored `data/desktop.ini`. Shared palette/control
  styles were adapted from the read-only
  [UDS-Analysis themes](https://github.com/dathp95/UDS-Analysis/tree/main/gui/themes)
  and [controls](https://github.com/dathp95/UDS-Analysis/tree/main/gui/widgets/controls)
  reference. There is no dependency on another checkout.

In Settings, choose an encrypted RSA private-key file, type its password in the
masked field, and explicitly choose **Unlock signing**. The password field is
cleared immediately; passwords/private-key contents are never saved. Only the
nonsecret file path may be remembered. The key stays in memory until **Lock
signing** or exit. Lock disables new issuance and invalidates any pending unlock;
a confirmed signing task already running finishes safely before shutdown. This
is reference release, not a guarantee of cryptographic memory erasure. Existing
tokens copied to the system clipboard remain there until replaced or cleared.

DB queries, key loading, and signing run in a bounded worker pool, with separate
SQLite connections and queued Qt signals for UI updates. Closing waits for active
operations; it does not terminate signing. **Refresh** reloads the current view
and preserves the selected ID when it remains on the page. **Refresh all** also
retries database initialization after a storage error. UTC stays in storage;
timestamps are converted to the system timezone for display, with timezone labels.

Desktop validation with temporary databases/keys and no messages:

```powershell
python -m unittest discover -s tests -v
python tests/preview_gui.py --output output/gui-validation
```

GUI tests are skipped if PySide6 is not installed; install `requirements-gui.txt`
to run them. The preview harness creates isolated data and captures Light/Dark
tabs, review/error dialogs, and a compact layout. It defaults to Qt's offscreen
platform; on Windows it registers installed fonts if offscreen font discovery is
unavailable. To use the native Windows renderer:

```powershell
$env:QT_QPA_PLATFORM = "windows"
python tests/preview_gui.py --output output/gui-validation-native
Remove-Item Env:QT_QPA_PLATFORM
```

Current desktop limits: no request approval/rejection, resending, automatic retry,
activation reporting, exports, remote mode, or production packaging. Refresh is
manual; there is no live database watcher. No production data is needed for tests
or preview capture.

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
Migration 002 upgrades commit-12 databases transactionally, preserving request,
license, exact token/payload, and delivery records. Migration 001 is unchanged.
Legacy `attempting` rows and recorded timeouts become `unknown`, since their
delivery outcome cannot be inferred. Back up before upgrading; commit 12 cannot
open the upgraded schema.

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
Use `/requests` (or `/requests 2`, etc.) for pages of at most eight pending or
issued-but-unsent requests, including failed and unknown deliveries. Page buttons
and the original `request:<id>` buttons select persisted requests after restart.
Unsent manual Telegram licenses belonging to Admin also appear with their
`/retry LICENSE_ID` recovery command; CLI output records remain in the database.

Selecting a request binds its persisted ID to a server-side form. Enter Days to
preview the customer, normalized Device ID, recipient, edition, duration, and
whether the existing key will be reused. **Entering Days does not issue or send a
key.** Press **Confirm** to proceed, or **Cancel** to discard it. The server checks
the admin, private chat, request/license/delivery snapshot, and expiry again at
confirmation. Forms expire after ten minutes and are single-use. Selecting another
request, navigating menus, `/requests`, `/generate`, or `/cancel` invalidates older
confirmation buttons for that admin/chat. Changed state requires a fresh preview.

For an unissued pending request, select **Từ chối yêu cầu**, enter a reason
(1–300 characters, no secrets), and confirm. Rejection records the admin, UTC time,
and reason, and closes that request without issuing a key. It does not revoke any
offline license. Issued requests cannot be rejected. A customer may submit a new
request for fresh admin review.

After a failed delivery, select the request again, enter its original duration,
review the stored-key preview, and explicitly confirm retry. The exact token,
payload, license ID, and recipient are reused; no replacement key is created.
Repeated approval cannot create a second license for a request. Successful
deliveries are blocked from resend, even when an old button or retry command is
repeated. Customer resubmissions report the persisted state: pending review,
issued/delivery pending, or already sent, rather than always claiming review is
pending.

For keys created manually with `/generate` or the admin menu, use the displayed
License ID with `/retry LICENSE_ID` to retry the same stored key to Admin,
including after restart. For an unknown outcome or permanent failure, the command
first explains the duplicate-message risk and requires the explicit
`/retry LICENSE_ID confirm` action. Already successful sends are blocked.
This command is admin-only. It does not create a license.
CLI licenses and output-attempt records can be recovered from the protected DB;
there is no CLI retry command in this upgrade.

Network failures and rate limits are recorded as retryable. Telegram `RetryAfter`
sets a persisted cooldown; another attempt for that delivery is refused until the
UTC deadline, including after restart and even with explicit retry. Timeouts have
an **unknown** outcome. Forbidden, invalid-chat/bad-request, and other Telegram
failures are recorded as permanent failures; fix the problem before explicitly
retrying. There is no automatic background retry worker. Expired keys are refused,
never silently replaced: the customer must submit a new request and obtain fresh
admin approval.

Every send atomically acquires an owned claim before network I/O. The send has a
30-second I/O deadline and a 120-second lease. Concurrent workers are blocked
while that claim is live; no DB transaction spans a network await. Completion
checks the attempt ID, owner, active state, and lease so an old worker cannot
finalize a newer attempt. The claim also checks the delivery state reviewed by
Admin, so an outcome that changes after Confirm requires a fresh confirmation.
Claims survive restart until their leases expire.
Startup, request-list access, previews, and claims recover expired `attempting`
records as `unknown`; they are never assumed successful or failed.

Unknown outcomes require explicit admin review and a **Confirm retry (may
duplicate)** action. First check whether the original message arrived and ensure
any hung old worker is stopped. Timeouts, suspended processes, or crashes can
occur after Telegram accepts a message but before acknowledgment is stored.
SQLite fencing cannot retract a network request already sent to Telegram. A
manual retry may therefore produce a duplicate message with the same key;
exactly-once Telegram delivery is **not guaranteed**. A DB failure while recording
acknowledgment also leaves the outcome uncertain until the claim expires.

**Issued** means the signed key is saved. **Sent** means Telegram acknowledged
delivery and that acknowledgment was saved. **Activated** means V-CODE accepted a
key on the customer's device; this tool does not receive or report that event.

Transient input steps may remain in RAM; confirmation state is stored server-side
in SQLite. New request buttons work after restart against the same database.
Pre-upgrade RAM-only requests cannot be recovered;
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
