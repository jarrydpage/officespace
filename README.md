# OfficeSpace

This repo uses two files:

- `auth.json` for cached OfficeSpace auth state
- `officespace.toml` for booking config

The Python package now lives under `src/officespace`.
Installing the repo exposes two console scripts:

- `officespace` for the multi-command auth and manual booking CLI
- `officespace-runner` for the env/config-driven booking run

`auth.json` stores the current `authToken`, the resolved `domain`, and token metadata such as `exp`, `iat`, and `sub`.

## How It Works

Registration is manual and CLI-only:

```bash
officespace register --auth-config-file auth.json
```

That command reads the OfficeSpace QR PNG, exchanges the registration token, and writes `auth.json`.

`officespace-runner` does not register. It only runs the booking flow:

1. Loads `officespace.toml` when present, with `OFFICESPACE_*` environment variables overriding file values.
2. Builds an auth context from `auth.json` or `OFFICESPACE_AUTH_TOKEN`.
3. Refreshes the auth token if needed.
4. Prepares the booking request.
5. Logs the prepared request in dry-run mode, or sends it in book mode.

Default behavior:

```bash
officespace-runner
```

Send the booking request:

```bash
OFFICESPACE_MODE=book officespace-runner
```

## Local Setup

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

That installs the local `officespace` package and makes both `officespace`
and `officespace-runner` available on your shell path.

If local registration cannot load `pyzbar`, install the `zbar` system library for your platform before running registration.

For registration, use either:

- `qr.png` in the project root
- `--qr-image-file /path/to/qr.png`
- `OFFICESPACE_QR_IMAGE_FILE=/path/to/qr.png`

Example `officespace.toml`:

```toml
[auth]
max_token_age = 1440

[booking]
floor_id = "106"
seat_id = "14869"
schedule = ["monday", "tuesday", "wednesday", "thursday"]
```

Equivalent environment variables can be used instead of creating `officespace.toml`.
For pipeline use, set `OFFICESPACE_FLOOR_ID`, `OFFICESPACE_SEAT_ID`, and exactly
one of `OFFICESPACE_BOOKING_DATE` or `OFFICESPACE_SCHEDULE`.
`OFFICESPACE_SCHEDULE` uses a JSON array value such as
`["monday", "tuesday", "wednesday", "thursday"]`.

Generate or refresh `auth.json`:

```bash
officespace register --auth-config-file auth.json
```

Or with an explicit QR path:

```bash
officespace register \
  --auth-config-file auth.json \
  --qr-image-file /path/to/qr.png
```

Print the current auth token:

```bash
officespace token --auth-config-file auth.json
```

## Pipeline Flow

The pipeline stores the full `auth.json` payload in the Azure Key Vault secret
named by `KEY_VAULT_AUTH_CONFIG_SECRET`.
The Azure Pipelines variable group `shared-officespace-prod` provides the Azure
connection settings plus the OfficeSpace booking configuration. The pipeline
installs the local `officespace` package and runs the `officespace-runner`
console script, so it does not need a committed or generated `officespace.toml`.

See `VARIABLES.md` for the full variable-group contract.

- `AZURE_SERVICE_CONNECTION`
- `KEY_VAULT_NAME`
- `KEY_VAULT_AUTH_CONFIG_SECRET`
- `OFFICESPACE_FLOOR_ID`
- `OFFICESPACE_SEAT_ID`
- exactly one of `OFFICESPACE_BOOKING_DATE` or `OFFICESPACE_SCHEDULE`

`OFFICESPACE_SCHEDULE` should be a JSON array value such as
`["monday", "tuesday", "wednesday", "thursday"]`.

Current job flow:

1. Download `auth.json` from Key Vault into `$(Agent.TempDirectory)/officespace-auth.json`.
2. Install the local `officespace` package.
3. Run `officespace-runner`.
4. Upload the resulting `auth.json` back to the same Key Vault secret.

The pipeline fails if the Key Vault secret does not already exist.

Upload the file manually the first time:

```bash
az login
az account set --subscription <subscription-id-or-name>

az keyvault secret set \
  --vault-name <KEY_VAULT_NAME> \
  --name <KEY_VAULT_AUTH_CONFIG_SECRET> \
  --file auth.json \
  --encoding utf-8
```

If you refresh auth locally later, upload the file again with the same command.

## Notes

- The variable group stores the Key Vault metadata, not the auth payload itself. The actual `auth.json` contents live in Key Vault under `KEY_VAULT_AUTH_CONFIG_SECRET`.
- Blank string env values are treated as unset.