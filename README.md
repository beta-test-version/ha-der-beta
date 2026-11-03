# ha EcoFlow official

[![HACS][hacs-badge]][hacs-url]
[![Home Assistant][ha-badge]][ha-url]
[![License][license-badge]][license-url]

Home Assistant custom integration that connects to EcoFlow inverters via **Modbus TCP**, exposing all register points as entities.

## Features

- **Full register coverage** — all documented Modbus points mapped to HA entities
- **Auto-discovery** — devices broadcast via mDNS (`_ecoflow._tcp.local.`), appear in HA automatically
- **Product filtering** — only loads registers supported by your device (STREAM 5000, STREAM AC 5000)
- **Region labels** — entity names start with the protocol region, such as
  `D1. Device serial number (SN)` and `S1. Current system fault count`.
- **Zero dependencies** — pymodbus is vendored; installs offline with no pip requirements
- **Shared Modbus connection** — on HA 2026.9+, reuses the built-in Modbus integration's connection so multiple integrations can share a single TCP link

## Home Assistant Energy dashboard

Reviewed read-only telemetry (power, voltage, current, temperature, frequency,
SOC/SOH, current counts, battery capacity and available battery power) provides
measurement statistics. Directional daily/lifetime energy counters use
`total_increasing`; battery capacity uses `energy_storage` with `measurement`.
Rated specifications, configuration limits, codes, identifiers, enum states,
timestamps and daily aggregates with unspecified net/throughput semantics keep
ordinary history. Writable controls retain their existing entities; no extra
readback sensors are created.
Select grid import/export, solar production and battery charge/discharge energy
sensors in the native Energy dashboard; live power and system SOC are also available.

## Supported Devices

| Device | Registers | Status |
|--------|-----------|--------|
| STREAM 5000 | Full | Supported |
| STREAM AC 5000 | Full | Supported |

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** menu → **Custom repositories**
3. Add `EcoFlow/modbus-API` with category **Integration**
4. Search for **ha EcoFlow official** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/ha_EcoFlow_official` directory into your Home Assistant `config/custom_components/` folder and restart.

## Configuration

### Auto-Discovery (Recommended)

Devices advertise themselves via mDNS. Home Assistant will show a discovery notification — just click **Configure** and confirm.

The `_ecoflow._tcp.local.` service TXT record uses `model` for the product
model (for example, `STREAM AC 5000`) and `serial` for the device SN.

### UI Manual Setup

**Settings** → **Devices & Services** → **Add Integration** → search **ha EcoFlow official** → enter IP and port. Connectivity is verified on submit.

Manual setup reads the device SN to select the model and its supported points:

| SN prefix | Product |
|-----------|---------|
| `ES21` | STREAM 5000 |
| `ES22` | STREAM AC 5000 |

### YAML (Advanced)

```yaml
ecoflow_modbus:
  - host: 192.168.1.10
    port: 502               # optional, default 502
    unit_id: 1              # optional, default 1
    scan_interval: 5        # optional, polling interval in seconds
```

## Entities

| Platform | Condition | Examples |
|----------|-----------|----------|
| Sensor | Read-only registers | System SOC, Grid power, PV voltage, Fault codes |
| Number | Writable numeric registers | Backup reserve ratio, Power limits, Setpoints |
| Select | Enumerated control registers | Operation mode, Feed-in mode, Control point |
| Switch | Boolean control registers | Bypass AC1/2/3, Force off-grid |
| Button | One-shot write registers | Heartbeat signal |

All entities are grouped under a single device per Modbus connection: **ha EcoFlow official (IP)**.

## Language Support

The integration adapts to your Home Assistant language setting. Unsupported languages fall back to English.

| Language | Status |
|----------|--------|
| English | ✅ |
| Deutsch | ✅ |
| Français | ✅ |
| Italiano | ✅ |
| Nederlands | ✅ |
| Polski | ✅ |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and contribution guidelines.

## License

This project is licensed under the [MIT License](LICENSE).

The vendored [pymodbus](https://github.com/pymodbus-dev/pymodbus) library is licensed under the [BSD-3-Clause License](custom_components/ha_EcoFlow_official/vendor/LICENSE).

---

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://hacs.xyz
[ha-badge]: https://img.shields.io/badge/HA-2025.1%2B-41BDF5.svg
[ha-url]: https://www.home-assistant.io
[license-badge]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: LICENSE
