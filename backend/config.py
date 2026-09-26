from pathlib import Path

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
