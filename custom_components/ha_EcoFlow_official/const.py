"""Constants for the EcoFlow Modbus integration."""

DOMAIN = "ecoflow_modbus"
PLATFORMS = ["sensor", "number", "select", "switch", "button"]

CONF_UNIT_ID = "unit_id"
CONF_PRODUCT = "product"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ADDRESS_OFFSET = "address_offset"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_ADDRESS_OFFSET = 40001


# Per-request timeout for Modbus calls (seconds)
LOCAL_MODBUS_TIMEOUT_S = 5
RECONNECT_INTERVAL_S = 3
RESPONSE_TIMEOUT_MULTIPLIER = 3

# UTC is a UINT32 register supported by every product, unlike protocol version.
RECONNECT_PROBE_OFFSET = 0x0201

# Heartbeat: the integration writes the keep-alive register itself, every
# HEARTBEAT_INTERVAL_S seconds (the protocol requires one within 60s).
HEARTBEAT_OFFSET = 0x025F
HEARTBEAT_INTERVAL_S = 20

# Registers that must NOT be exposed as entities.
EXCLUDED_OFFSETS = {0x025F}  # heartbeat (integration-managed keep-alive)

# Registers rendered as one-shot buttons: offset -> value written on press
BUTTON_WRITES = {}

# Registers rendered as toggles: offset -> (value when ON, value when OFF)
SWITCHES = {
    0x2403: (1, 0),  # Bypass AC1 switch
    0x2406: (1, 0),  # Bypass AC2 switch
    0x2409: (1, 0),  # Bypass AC3 switch
    0x0278: (1, 0),  # Force off-grid (AU)
    0x0276: (1, 2),  # Soft start switch (AU): 1=on, 2=off
}

# Registers rendered as dropdowns: offset -> {value: label}
SELECTS = {
    0x0218: {0: "Limited mode", 1: "Unlimited mode", 2: "Percentage limit"},
    0x0263: {0: "Remote", 1: "Local"},
    0x0277: {0: "Allow charge & discharge", 1: "Charge disabled",
             2: "Discharge disabled", 3: "Both disabled"},
    0x022D: {1: "Self-use", 2: "AI mode"},
    0x0275: {0: "Self-use", 1: "System power", 2: "Inverter power",
             3: "Battery power"},
    0x1610: {0: "None", 1: "Battery point", 2: "Meter point",
             3: "Inverter point", 4: "Grid point"},
}
