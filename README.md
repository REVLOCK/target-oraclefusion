# target-oracle-fusion

`target-oracle-fusion` is a Singer target for Oracle-Fusion.

Build with the [Meltano Target SDK](https://sdk.meltano.com).

<!--

Developer TODO: Update the below as needed to correctly describe the install procedure. For instance, if you do not have a PyPi repo, or if you want users to directly install from your git repo, you can modify this step as appropriate.

## Installation

Install from PyPi:

```bash
pipx install target-oracle-fusion
```

Install from GitHub:

```bash
pipx install git+https://github.com/ORG_NAME/target-oracle-fusion.git@main
```

-->

## Configuration

### Accepted Config Options

<!--
Developer TODO: Provide a list of config options accepted by the target.

This section can be created by copy-pasting the CLI output from:

```
target-oracle-fusion --about --format=markdown
```
-->

A full list of supported settings and capabilities for this
target is available by running:

```bash
target-oracle-fusion --about
```

### Configure using environment variables

This Singer target will automatically import any environment variables within the working directory's
`.env` if the `--config=ENV` is provided, such that config values will be considered if a matching
environment variable is set either in the terminal context or in the `.env` file.

### Source Authentication and Authorization

<!--
Developer TODO: If your target requires special access on the destination system, or any special authentication requirements, provide those here.
-->

## CSV Transform (Oracle Fusion GL Format)

Transform RevRec journal entries CSV to Oracle Fusion GL format and zip the output:

```bash
target-oracle-fusion --config config.json
```

(Same approach as target-intacct: main → upload → load_journal_entries)

### Config options

| Key | Description |
|-----|-------------|
| `input_path` | Path to input CSV file or directory containing `JournalEntries.csv` |
| `output_path` | Path for output directory (or .csv file) |
| `ledger_id` | Oracle ledger ID (default: 300000003864052) |
| `user_je_source_name` | Journal source name (default: Chargebee) |
| `user_je_category_name` | Journal category (default: Manual) |
| `ledger_name` | Ledger name (default: USA PL USD US GAAP) |
| `base_url` | Oracle Fusion base URL (required for upload) |
| `parameter_list` | ESS job parameters (default: `ledger_id1,ledger_id2,ledger_id3,ALL,N,N,N`) |

**Authentication** (JWT): `jwt_issuer`, `jwt_principal`, `jwt_private_key` (PEM string in config).
- optional `jwt_x5t`

### Example config.json

```json
{
  "input_path": "./data/JournalEntries.csv",
  "output_path": "./output",
  "ledger_id": "300000003864052",
  "user_je_source_name": "Chargebee",
  "user_je_category_name": "Manual",
  "ledger_name": "USA PL USD US GAAP"
}
```

### CLI options

- `--config` / `-c` - Path to JSON config file (required)
- `-v` / `--verbose` - Verbose logging

### Input CSV format

Required columns: Transaction Date, Journal Entry Id, Account Number, Account Name, Description, Amount, Posting Type, Currency.

Optional: Department, Location, Discord Channel, Class, Customer Name, Tier.

---

## Usage

You can easily run `target-oracle-fusion` by itself or in a pipeline using [Meltano](https://meltano.com/).

### Executing the Target Directly

```bash
target-oracle-fusion --version
target-oracle-fusion --help
# Test using the "Carbon Intensity" sample:
tap-carbon-intensity | target-oracle-fusion --config /path/to/target-oracle-fusion-config.json
```

## Developer Resources

Follow these instructions to contribute to this project.

### Initialize your Development Environment

```bash
pipx install poetry
poetry install
```

### Create and Run Tests

Create tests within the `tests` subfolder and
  then run:

```bash
poetry run pytest
```

You can also test the `target-oracle-fusion` CLI interface directly using `poetry run`:

```bash
poetry run target-oracle-fusion --help
```

### Testing with [Meltano](https://meltano.com/)

_**Note:** This target will work in any Singer environment and does not require Meltano.
Examples here are for convenience and to streamline end-to-end orchestration scenarios._

<!--
Developer TODO:
Your project comes with a custom `meltano.yml` project file already created. Open the `meltano.yml` and follow any "TODO" items listed in
the file.
-->

Next, install Meltano (if you haven't already) and any needed plugins:

```bash
# Install meltano
pipx install meltano
# Initialize meltano within this directory
cd target-oracle-fusion
meltano install
```

Now you can test and orchestrate using Meltano:

```bash
# Test invocation:
meltano invoke target-oracle-fusion --version
# OR run a test `elt` pipeline with the Carbon Intensity sample tap:
meltano run tap-carbon-intensity target-oracle-fusion
```

### SDK Dev Guide

See the [dev guide](https://sdk.meltano.com/en/latest/dev_guide.html) for more instructions on how to use the Meltano Singer SDK to
develop your own Singer taps and targets.
