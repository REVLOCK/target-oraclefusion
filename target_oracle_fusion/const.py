"""Defaults, column lists, and API paths."""

# Singer parse_args sees raw JSON only; custom_fields nested keys are not listed here.
REQUIRED_CONFIG_KEYS = ["input_path"]

# After flatten_config, each key must be present and non-empty.
REQUIRED_FLATTENED_CONFIG_KEYS = [
    "LEDGER_ID",
    "LEDGER_NAME",
    "Entity",
    "Intercompany",
    "base_url",
    "category_name",
    "input_path",
    "jwt_issuer",
    "jwt_principal",
    "jwt_x5t",
    "parameter_list",
    "private_key",
    "source_name",
]

# Run workspace; cleared after upload (success or failure).
DEFAULT_OUTPUT_PATH = "./output"
ESS_SCRATCH_DIRNAME = ".ess_scratch"

REQUIRED_INPUT_COLUMNS = [
    "Transaction Date",
    "Journal Entry Id",
    "Account Number",
    "Account Name",
    "Description",
    "Amount",
    "Posting Type",
    "Currency",
    "Department",
    "Location",
    "Discord Channel"
]

INPUT_FILENAME = "JournalEntries.csv"
OUTPUT_FILENAME = "GL_INTERFACE"
ZIP_FILENAME_PREFIX = "Glinterface_chargebee"

ORACLE_OUTPUT_COLUMNS = [
    "STATUS",
    "LEDGER_ID",
    "ACCOUNTING_DATE",
    "USER_JE_SOURCE_NAME",
    "USER_JE_CATEGORY_NAME",
    "CURRENCY_CODE",
    "DATE_CREATED",
    "ACTUAL_FLAG",
    "SEGMENT1",
    "SEGMENT2",
    "SEGMENT3",
    "SEGMENT4",
    "SEGMENT5",
    "SEGMENT6",
    "SEGMENT7",
    "SEGMENT8",
    "SEGMENT9",
    "SEGMENT10",
    "SEGMENT11",
    "SEGMENT12",
    "SEGMENT13",
    "SEGMENT14",
    "SEGMENT15",
    "SEGMENT16",
    "SEGMENT17",
    "SEGMENT18",
    "SEGMENT19",
    "SEGMENT20",
    "SEGMENT21",
    "SEGMENT22",
    "SEGMENT23",
    "SEGMENT24",
    "SEGMENT25",
    "SEGMENT26",
    "SEGMENT27",
    "SEGMENT28",
    "SEGMENT29",
    "SEGMENT30",
    "ENTERED_DR",
    "ENTERED_CR",
    "ACCOUNTED_DR",
    "ACCOUNTED_CR",
    "REFERENCE1",
    "REFERENCE2",
    "REFERENCE3",
    "REFERENCE4",
    "REFERENCE5",
    "REFERENCE6",
    "REFERENCE7",
    "REFERENCE8",
    "REFERENCE9",
    "REFERENCE10",
    "REFERENCE21",
    "REFERENCE22",
    "REFERENCE23",
    "REFERENCE24",
    "REFERENCE25",
    "REFERENCE26",
    "REFERENCE27",
    "REFERENCE28",
    "REFERENCE29",
    "REFERENCE30",
    "STAT_AMOUNT",
    "USER_CURRENCY_CONVERSION_TYPE",
    "CURRENCY_CONVERSION_DATE",
    "CURRENCY_CONVERSION_RATE",
    "GROUP_ID",
    "ATTRIBUTE_CATEGORY",
    "ATTRIBUTE1",
    "ATTRIBUTE2",
    "ATTRIBUTE3",
    "ATTRIBUTE4",
    "ATTRIBUTE5",
    "ATTRIBUTE6",
    "ATTRIBUTE7",
    "ATTRIBUTE8",
    "ATTRIBUTE9",
    "ATTRIBUTE10",
    "ATTRIBUTE11",
    "ATTRIBUTE12",
    "ATTRIBUTE13",
    "ATTRIBUTE14",
    "ATTRIBUTE15",
    "ATTRIBUTE16",
    "ATTRIBUTE17",
    "ATTRIBUTE18",
    "ATTRIBUTE19",
    "ATTRIBUTE20",
    "ATTRIBUTE_CATEGORY3",
    "AVERAGE_JOURNAL_FLAG",
    "ORIGINATING_BAL_SEG_VALUE",
    "LEDGER_NAME",
    "ENCUMBRANCE_TYPE_ID",
    "JGZZ_RECON_REF",
    "PERIOD_NAME",
    "REFERENCE18",
    "REFERENCE19",
    "REFERENCE20",
    "ATTRIBUTE_DATE1",
    "ATTRIBUTE_DATE2",
    "ATTRIBUTE_DATE3",
    "ATTRIBUTE_DATE4",
    "ATTRIBUTE_DATE5",
    "ATTRIBUTE_DATE6",
    "ATTRIBUTE_DATE7",
    "ATTRIBUTE_DATE8",
    "ATTRIBUTE_DATE9",
    "ATTRIBUTE_DATE10",
    "ATTRIBUTE_NUMBER1",
    "ATTRIBUTE_NUMBER2",
    "ATTRIBUTE_NUMBER3",
    "ATTRIBUTE_NUMBER4",
    "ATTRIBUTE_NUMBER5",
    "ATTRIBUTE_NUMBER6",
    "ATTRIBUTE_NUMBER7",
    "ATTRIBUTE_NUMBER8",
    "ATTRIBUTE_NUMBER9",
    "ATTRIBUTE_NUMBER10",
    "GLOBAL_ATTRIBUTE_CATEGORY",
    "GLOBAL_ATTRIBUTE1",
    "GLOBAL_ATTRIBUTE2",
    "GLOBAL_ATTRIBUTE3",
    "GLOBAL_ATTRIBUTE4",
    "GLOBAL_ATTRIBUTE5",
    "GLOBAL_ATTRIBUTE6",
    "GLOBAL_ATTRIBUTE7",
    "GLOBAL_ATTRIBUTE8",
    "GLOBAL_ATTRIBUTE9",
    "GLOBAL_ATTRIBUTE10",
    "GLOBAL_ATTRIBUTE11",
    "GLOBAL_ATTRIBUTE12",
    "GLOBAL_ATTRIBUTE13",
    "GLOBAL_ATTRIBUTE14",
    "GLOBAL_ATTRIBUTE15",
    "GLOBAL_ATTRIBUTE16",
    "GLOBAL_ATTRIBUTE17",
    "GLOBAL_ATTRIBUTE18",
    "GLOBAL_ATTRIBUTE19",
    "GLOBAL_ATTRIBUTE20",
    "GLOBAL_ATTRIBUTE_DATE1",
    "GLOBAL_ATTRIBUTE_DATE2",
    "GLOBAL_ATTRIBUTE_DATE3",
    "GLOBAL_ATTRIBUTE_DATE4",
    "GLOBAL_ATTRIBUTE_DATE5",
    "GLOBAL_ATTRIBUTE_NUMBER1",
    "GLOBAL_ATTRIBUTE_NUMBER2",
    "GLOBAL_ATTRIBUTE_NUMBER3",
    "GLOBAL_ATTRIBUTE_NUMBER4",
    "GLOBAL_ATTRIBUTE_NUMBER5",
    "CREATION_DATE",
]

# REST erpintegrations
ERP_INTEGRATIONS_PATH = "/fscmRestApi/resources/11.13.18.05/erpintegrations"

# GL journal import job (defaults; override via config where supported)
DEFAULT_DOCUMENT_ACCOUNT = "fin$/generalLedger$/import$"
DEFAULT_JOB_NAME = "/oracle/apps/ess/financials/generalLedger/programs/common,JournalImportLauncher"
DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_MAX_WAIT_SECONDS = 1800

# BI SOAP report (ESS status)
ESS_REPORT_SOAP_PATH = "/xmlpserver/services/ExternalReportWSSService"
DEFAULT_ESS_JOB_REPORT_PATH = "/Custom/Financials/XXDISCORD/XXDIS_ESSJobDetails_Report.xdo"
ESS_MIN_ROWS_FOR_ERROR_LOG = 6
ESS_SIXTH_ROW_INDEX = 5  # 0-based index for 6th row

# Hotglue job runtime (``source-config.json`` ``output_path_prefix`` uses these from env).
HOTGLUE_ENV_TENANT = "TENANT"
HOTGLUE_ENV_FLOW = "FLOW"
HOTGLUE_ENV_JOB_ID = "JOB_ID"

# ESS error log S3: read from ``source-config.json`` next to job root (``ROOT_DIR`` or ``.``).
# If set to 1/true/yes, logs the entire file at WARNING when loaded (may contain secrets).
ENV_ESS_PRINT_SOURCE_CONFIG_FULL = "ESS_PRINT_SOURCE_CONFIG_FULL"
# Optional path to JSON; otherwise we try ``ROOT_DIR``, cwd, then parents (targets often cwd under ``targets/<id>/``).
ENV_ESS_SOURCE_CONFIG_PATH = "ESS_SOURCE_CONFIG_PATH"
SOURCE_CONFIG_FILENAME = "source-config.json"
# When walking up from cwd looking for ``SOURCE_CONFIG_FILENAME`` (after ``ROOT_DIR`` / cwd checks).
SOURCE_CONFIG_PARENT_WALK_MAX = 10
SOURCE_CONFIG_KEY_AWS_ACCESS_KEY_ID = "aws_access_key_id"
SOURCE_CONFIG_KEY_AWS_SECRET_ACCESS_KEY = "aws_secret_access_key"
SOURCE_CONFIG_KEY_AWS_REGION = "aws_region"
SOURCE_CONFIG_KEY_BUCKET = "bucket"
SOURCE_CONFIG_KEY_OUTPUT_PATH_PREFIX = "output_path_prefix"
DEFAULT_ESS_ERROR_LOG_S3_REGION = "us-east-1"

# ESS job status values
ESS_STATUS_FAILURE = ("ERROR", "FAILED", "CANCELLED", "WARNING")
ESS_STATUS_SUCCESS = ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "COMPLETED")
