# Docker model credential persistence

PAAM's normal desktop process uses the operating-system keyring. A headless
Docker container without a configured keyring cannot save model API keys, even
when its SQLite database is on a persistent volume. The encrypted-file backend
is an explicit alternative for the project's single-process Docker profile.

## Provision

1. Create a directory outside the repository and outside the `/data` volume,
   restrict access to the Docker operator, then run
   `python src/script/create_credential_key.py <absolute-host-key-path>`.
   The script refuses to overwrite an existing key and never prints it.
2. Mount the existing named data volume at `/data`. Mount the key file read-only
   at `/run/secrets/paam_credential_key` (a Compose file-backed secret or a
   read-only Docker bind mount).
3. Set `PAAM_CREDENTIAL_STORE=encrypted_file` and
   `PAAM_CREDENTIAL_KEY_FILE=/run/secrets/paam_credential_key` for the container.
   Keep real-analysis flags off until the operator separately authorizes
   provider calls.

For Compose, the relevant service configuration is:

```yaml
services:
  paam:
    environment:
      PAAM_CREDENTIAL_STORE: encrypted_file
      PAAM_CREDENTIAL_KEY_FILE: /run/secrets/paam_credential_key
    volumes:
      - paam-data:/data
    secrets:
      - paam_credential_key
secrets:
  paam_credential_key:
    file: /absolute/operator-controlled/path/paam-credential.key
volumes:
  paam-data:
```

The file `/data/model-credentials.fernet` contains authenticated ciphertext;
no API key is written to SQLite or the image. The key file must not be in the
data volume, source tree, image, environment variable, or logs. Compose's
file-backed secret is a read-only bind mount, not an encrypted host-side vault:
protect its source file with host filesystem permissions.

Back up the **data volume and key file separately**. Restore both together to
recover credentials. Losing or replacing the key makes the encrypted file
unreadable; PAAM fails closed instead of silently treating models as unconfigured
or generating a new key. Do not delete either file to troubleshoot a failed
startup. A native-OS keyring deployment remains unchanged. Existing Docker
models whose keys never saved must be given their API keys again after this
backend is configured; stale enabled models without keys must be repaired or
disabled before another model can be saved.

Verify with a disposable volume and fictional key: save an enabled model,
confirm `key_configured=true`, recreate the container with the same data volume
and same mounted key, and confirm it remains true. A deliberately wrong or
missing key must prevent startup. Do not use a real provider key or enable
real analysis for this verification.
