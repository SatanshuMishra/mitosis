__version__ = "0.1.0"

PLAN_KEYS = (
    "version",
    "plan_id",
    "source",
    "items",
    "msps",
    "lanes",
    "lane_after",
    "clusters",
    "tiers",
    "verify_modes",
    "context_packs",
    "lane_order",
    "coalesce",
    "coupling_review",
    "counts",
    "briefs",
)

ITEM_FIELDS = (
    "name",
    "task",
    "files",
    "source",
    "acceptance",
    "after",
    "contract_group",
    "type",
    "complexity",
    "file_notes",
    "msp",
    "spec_ref",
    "assumptions",
)

REQUIRED_ITEM_FIELDS = (
    "name",
    "task",
    "files",
    "source",
    "acceptance",
)

ACCEPTANCE_KEYS = ("file", "test")

SOURCE_KEYS = ("path", "sha256")

LANE_STATES = ("ok", "failed", "blocked", "merge-blocked")

MSP_STATES = ("shipped", "gate-failed", "gate-inconclusive", "ship-failed")

GATE_OUTCOMES = ("pass", "inert", "inconclusive", "not-applicable")

STEP_TYPES = ("fix", "feature", "port", "design", "sweep", "contract")

COMPLEXITY_VALUES = ("simple", "complex")

TIERS = ("top", "cheap")

VERIFY_MODES = ("serial", "offload")

COUPLING_SIGNALS = (
    "import-adjacency",
    "shared-risk-marker",
    "recorded-regression",
    "same-migration-directory",
)

COUNT_KEYS = ("missing_paths", "no_acceptance", "assumptions")

RETURN_KEYS = ("item", "status", "files_changed", "notes")

NOTES_CAP = 200

RETURN_CONTRACT = (
    '{"item":"<name>","status":"ok"|"failed",'
    '"files_changed":["path",...],"notes":"<=200 chars"}'
)

FLAG_NAMES = (
    "--items",
    "--spec",
    "--charter",
    "--feature-branch",
    "--run-dir",
    "--resume",
    "--plan-only",
    "--dispatch-command",
    "--decompose-command",
    "--acceptance-command",
    "--pr-command",
    "--tier-model",
    "--timeout",
    "--concurrency",
    "--context-hops",
    "--context-cap",
    "--graph",
    "--risk-markers",
    "--serial-markers",
    "--trajectory",
    "--version",
    "--help",
)
