# target-oracle-fusion

Singer target that reads journal CSVs, builds Oracle Fusion GL interface files, zips them, and uploads via Oracle Fusion REST (JWT) with ESS job polling.

Repository: [github.com/REVLOCK/target-oraclefusion](https://github.com/REVLOCK/target-oraclefusion)

## Quick Start

### 1. Install

```bash
pip install git+https://github.com/REVLOCK/target-oraclefusion.git
```

Or with `pipx`:

```bash
pipx install "git+https://github.com/REVLOCK/target-oraclefusion.git@main"
```

### 2. Create `config.json`

`input_path` must be top-level (required by Singer). Other settings can be top-level or under `custom_fields` (see `config.sample.json`); top-level wins on duplicate names.

Example (replace placeholders with your Fusion pod, ledger, JWT, and `parameter_list` from your Journal Import job):

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

Put `JournalEntries.csv` under `input_path` (or set `input_path` to the folder that contains it).

### 3. Run

```bash
target-oracle-fusion --config config.json
```

Artifacts use `./output` for the run (workspace is cleared after upload). See `target_oracle_fusion/const.py` for defaults (poll interval, job name, etc.).

For more settings, run:

```bash
target-oracle-fusion --about
```
