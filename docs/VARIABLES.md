# Variable Group Contract

This pipeline expects one Azure DevOps variable group named `shared-officespace-prod`.

## Required

- `AZURE_SERVICE_CONNECTION`: Azure DevOps service connection name used by the `AzureCLI@2` tasks. It must have Key Vault secret `get`, `list`, and `set` access.
- `KEY_VAULT_NAME`: Key Vault that stores the OfficeSpace auth cache file.
- `KEY_VAULT_AUTH_CONFIG_SECRET`: Secret name in that Key Vault that contains the full `auth.json` payload.
- `OFFICESPACE_FLOOR_ID`: OfficeSpace floor ID to book against.
- `OFFICESPACE_SEAT_ID`: OfficeSpace seat ID to book.

## Exactly One Required

- `OFFICESPACE_BOOKING_DATE`: Single booking date in `YYYY-MM-DD` format.
- `OFFICESPACE_SCHEDULE`: JSON array of weekdays, for example `["monday", "tuesday", "wednesday", "thursday"]`.

Set exactly one of `OFFICESPACE_BOOKING_DATE` or `OFFICESPACE_SCHEDULE`.

## Optional

- `OFFICESPACE_SITE_ID`: Site ID override. Leave unset to let the app resolve the site from the seat.
- `OFFICESPACE_EMPLOYEE_ID`: Employee ID override. Leave unset to let the app resolve the current user.
- `OFFICESPACE_MAX_TOKEN_AGE`: Max auth token age in minutes before a forced refresh.
- `OFFICESPACE_DOMAIN`: Required only when the cached `auth.json` does not already contain the domain.

## Not Stored In The Variable Group

- `OFFICESPACE_AUTH_CONFIG_FILE`: Set by the pipeline to `$(Agent.TempDirectory)/officespace-auth.json`.
- `OFFICESPACE_MODE`: Kept in the pipeline as `book`.
- `PYTHON_VERSION`: Kept in the pipeline as the runner version selector.

## Notes

- The variable group stores the Key Vault metadata, not the auth payload itself. The actual `auth.json` contents live in Key Vault under `KEY_VAULT_AUTH_CONFIG_SECRET`.
- Blank string env values are treated as unset.