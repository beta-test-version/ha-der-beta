"""Product identification table. Product names must match register products.

Add a model and its SN prefixes here, then add the exact same model name to
the supported points' ``products`` lists in the source register table.
Regenerate registers_schema.py from that table. Restart or reload the
integration after updating profiles and register definitions.
"""

DEFAULT_PRODUCT = "PowerOcean"

# One model may have multiple prefixes. The longest matching prefix wins.
PRODUCT_SN_PREFIXES: dict[str, tuple[str, ...]] = {
    "STREAM 5000": ("ES21",),
    "STREAM AC 5000": ("ES22",),
}


def product_from_serial(serial: str | None) -> str:
    """Resolve an SN using the product table, defaulting to PowerOcean."""
    normalized = (serial or "").strip().upper()
    product = DEFAULT_PRODUCT
    longest = 0
    for name, prefixes in PRODUCT_SN_PREFIXES.items():
        for prefix in prefixes:
            prefix = prefix.strip().upper()
            if prefix and len(prefix) > longest and normalized.startswith(prefix):
                product = name
                longest = len(prefix)
    return product
