"""Copy to relay_config.py and keep the real values out of Git.

The endpoint is the public HTTPS address of the pit-wall server followed by
/api/vehicle/exchange. A stable domain is recommended for field operation.
"""

RELAY_URL = "https://YOUR-STABLE-DOMAIN.example/api/vehicle/exchange"
RELAY_TOKEN = "REPLACE_WITH_A_LONG_RANDOM_TOKEN"
VEHICLE_ID = "A"
