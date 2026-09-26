from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data" / "Wilderness North"
STAGE1_DIR = DATA_DIR / "Stage 1"
STAGE2_DIR = DATA_DIR / "Stage 2"

STAGE1_ORDERS_FILE = STAGE1_DIR / "contestant_stage1_orders (1).csv"
STAGE2_ORDERS_FILE = STAGE2_DIR / "contestant_stage2_orders.csv"
FLIGHT_CAPACITY_FILE = STAGE2_DIR / "flight_capacity_stage2.csv"

# Wilderness North returnable tote dimensions
TOTE_LENGTH_IN = 23.5
TOTE_WIDTH_IN = 14.0
TOTE_HEIGHT_IN = 11.0

TOTE_VOLUME_IN3 = (
    TOTE_LENGTH_IN
    * TOTE_WIDTH_IN
    * TOTE_HEIGHT_IN
)

TOTE_VOLUME_FT3 = TOTE_VOLUME_IN3 / 1728.0

# Picking
DEFAULT_TOTES_PER_CART = 5

# Aircraft limits stated in the challenge writeup for the Stage 1 base case.
# No usable cargo-volume limit is stated; do not derive one from approximate cabin dimensions.
# Stage 2 uses the per-departure CSV capacities instead.
DEFAULT_AIRCRAFT_MAX_TOTES = 90
DEFAULT_AIRCRAFT_PAYLOAD_LB = 2877.0

APP_NAME = "Wilderness North Fulfillment"
APP_VERSION = "0.1.0"


def _csv_environment(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    values = default if raw is None else tuple(value.strip().rstrip("/") for value in raw.split(",") if value.strip())
    if "*" in values:
        raise ValueError(f"{name} does not allow a wildcard origin")
    for origin in values:
        if not (origin.startswith("https://") or origin.startswith("http://")):
            raise ValueError(f"{name} entries must be explicit http(s) origins")
        if "/" in origin.split("://", 1)[1]:
            raise ValueError(f"{name} entries must be origins without a path")
    return values


# Local mode preserves Vite's default development origins. Production must be
# started with DEPLOYMENT_MODE=single_operator and explicit deployment settings.
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "development").strip().lower()
if DEPLOYMENT_MODE not in {"development", "single_operator"}:
    raise ValueError("DEPLOYMENT_MODE must be 'development' or 'single_operator'")
CORS_ALLOWED_ORIGINS = _csv_environment(
    "CORS_ALLOWED_ORIGINS",
    ("http://localhost:5173", "http://127.0.0.1:5173"),
)
OPERATOR_SESSION_TIMEOUT_MINUTES = int(os.getenv("OPERATOR_SESSION_TIMEOUT_MINUTES", "240"))
if OPERATOR_SESSION_TIMEOUT_MINUTES < 5:
    raise ValueError("OPERATOR_SESSION_TIMEOUT_MINUTES must be at least 5")
OPERATOR_PROCESS_LOCK_PATH = Path(os.getenv("OPERATOR_PROCESS_LOCK_PATH", "/tmp/wilderness-north-operator.lock"))
OPERATOR_COOKIE_NAME = "wilderness_north_operator"
OPERATOR_COOKIE_SECURE = os.getenv("OPERATOR_COOKIE_SECURE", "true" if DEPLOYMENT_MODE == "single_operator" else "false").lower() in {"1", "true", "yes"}
OPERATOR_COOKIE_SAMESITE = os.getenv("OPERATOR_COOKIE_SAMESITE", "lax").strip().lower()
if OPERATOR_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise ValueError("OPERATOR_COOKIE_SAMESITE must be lax, strict, or none")
if OPERATOR_COOKIE_SAMESITE == "none" and not OPERATOR_COOKIE_SECURE:
    raise ValueError("SameSite=None operator cookies require OPERATOR_COOKIE_SECURE=true")
