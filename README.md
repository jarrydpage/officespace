# OfficeSpace Automate

This repo uses two files:

- `auth.json` for cached OfficeSpace auth state
- `run.toml` for booking config

`auth.json` stores the current `authToken`, the resolved `domain`, and token metadata such as `exp`, `iat`, and `sub`.

## How It Works

Registration is manual and CLI-only:

```bash
python -m officespace register --auth-config-file auth.json
```

That command reads the OfficeSpace QR PNG, exchanges the registration token, and writes `auth.json`.

`python run.py` does not register. It only runs the booking flow:

1. Loads `run.toml`.
2. Builds an auth context from `auth.json` or `OFFICESPACE_AUTH_TOKEN`.
3. Refreshes the auth token if needed.
4. Prepares the booking request.
5. Logs the prepared request in dry-run mode, or sends it in book mode.

Default behavior:

```bash
python run.py
```

Send the booking request:

```bash
OFFICESPACE_MODE=book python run.py
```

## Local Setup

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If local registration cannot load `pyzbar`, install the `zbar` system library for your platform before running registration.

For registration, use either:

- `qr.png` in the project root
- `--qr-image-file /path/to/qr.png`
- `OFFICESPACE_QR_IMAGE_FILE=/path/to/qr.png`

Example `run.toml`:

```toml
[auth]
max_token_age = 1440

[booking]
floor_id = "106"
seat_id = "14869"
schedule = ["monday", "tuesday", "wednesday", "thursday"]
```

Generate or refresh `auth.json`:

```bash
python -m officespace register --auth-config-file auth.json
```

Or with an explicit QR path:

```bash
python -m officespace register \
  --auth-config-file auth.json \
  --qr-image-file /path/to/qr.png
```

Print the current auth token:

```bash
python -m officespace token --auth-config-file auth.json
```

## Pipeline Flow

The pipeline stores the full `auth.json` payload in Azure Key Vault as the `officespace-auth-json` secret.

Current job flow:

1. Download `auth.json` from Key Vault into `$(Agent.TempDirectory)/officespace-auth.json`.
2. Run `python run.py`.
3. Upload the resulting `auth.json` back to the same Key Vault secret.

The pipeline fails if the Key Vault secret does not already exist.

Upload the file manually the first time:

```bash
az login
az account set --subscription <subscription-id-or-name>

az keyvault secret set \
  --vault-name zaekvpcs0001 \
  --name officespace-auth-json \
  --file auth.json \
  --encoding utf-8
```

If you refresh auth locally later, upload the file again with the same command.