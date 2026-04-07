# target-oracle-fusion

Singer target: journal CSV → GL interface file → zip → REST upload (JWT) and ESS polling.

Repo: [github.com/REVLOCK/target-oraclefusion](https://github.com/REVLOCK/target-oraclefusion)

## Quick start

**Install**

```bash
pip install git+https://github.com/REVLOCK/target-oraclefusion.git
# or: pipx install "git+https://github.com/REVLOCK/target-oraclefusion.git@main"
```

**Config** (`config.sample.json`)

`input_path` must be top-level (Singer). Other keys: top-level or `custom_fields`; top-level wins on duplicates.

Example (replace placeholders):

```json
{
  "input_path": "./data",
  "base_url": "https://your-pod.fa.ocs.oraclecloud.com",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
  "source_name": "Chargebee",
  "category_name": "Revenue",
  "LEDGER_ID": "900000004271828",
  "LEDGER_NAME": "Your Ledger Name",
  "Entity": "110",
  "Intercompany": "000",
  "parameter_list": "id1,id2,ledgerId,ALL,N,N,N",
  "jwt_issuer": "YourIntegrationClient",
  "jwt_principal": "integration.user@example.com",
  "jwt_x5t": "certificate-thumbprint-base64url="
}
```

Place `JournalEntries.csv` under `input_path`.

**Run**

```bash
target-oracle-fusion --config config.json
target-oracle-fusion --about
```

Artifacts under `./output` (cleared after upload). Defaults: `target_oracle_fusion/const.py`.
