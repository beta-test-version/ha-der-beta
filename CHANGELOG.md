# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Prefix entity display names with protocol regions (`D1.`, `S1.`, etc.)
  in English, German, French, Italian, Dutch and Polish.
- Editable product profile table with multiple SN prefixes per model and
  longest-prefix matching for future model extensions.

### Fixed
- Correct device error code 10 from offset `0x2020` to `0x201A` (Modbus
  address 48219) and migrate its existing sensor without changing the entity ID.
- Give writable numbers valid numeric bounds, including negative INT16/INT32
  values, and use input boxes for wide ranges so power setpoints can be entered.
- Use the `model` TXT field for mDNS product identification instead of `product`.
- Identify manually configured products by SN prefix (ES21: STREAM 5000,
  ES22: STREAM AC 5000, otherwise PowerOcean) before loading supported registers.
- Correct existing manual entries on reload, and reload entities if the serial
  becomes available only after the initial setup.
- Match manual setup, YAML import and mDNS discovery against both stored serial
  numbers and connection endpoints, including legacy entries with older IDs.
- Persist serial numbers read from Modbus so rediscovery can recognize existing
  entries after a restart or IP address change without replacing entity IDs.
- Recheck discovery confirmation to avoid duplicates added while the form is open.

## [1.2.0] - 2025-10-15

### Added
- Multi-language support: English, German, French, Italian, Dutch, Polish
- Automatic language detection from Home Assistant settings
- STREAM 5000 product support with dedicated registers
- Battery pack registers (SOC, SOH, SN) for up to 12 packs

### Changed
- Entity names now use Home Assistant's native translation system
- Select and enum entities use translated state labels

## [1.1.0] - 2025-09-01

### Added
- mDNS auto-discovery (`_ecoflow._tcp.local.`)
- YAML configuration import
- Product-based register filtering
- Heartbeat keep-alive (every 20s)

## [1.0.0] - 2025-08-01

### Added
- Initial release
- Modbus TCP connection to EcoFlow inverters
- Sensor, Number, Select, Switch, Button entity platforms
- PowerOcean product support
- Vendored pymodbus 3.7.4 (zero external dependencies)
