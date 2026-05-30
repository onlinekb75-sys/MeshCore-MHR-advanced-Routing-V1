# Flashen — NHR-MeshCore auf Heltec V4 (ESP32-S3, Repeater)

Diese beiden Images entstehen aus `pio run -e heltec_v4_repeater` (Phase 0 + Phase 1).
Sie sind lokale Build-Artefakte (`.bin` ist in `.gitignore`) — GitHub Actions baut dieselben
Images reproduzierbar (siehe `.github/workflows/build.yml`).

> ⚠️ Experimentell und auf Hardware ungetestet. **Zuerst auf ein Bench-/Ersatzgerät flashen,
> nicht auf einen produktiven Repeater.** Vorher Backup der Node-Identität/Config anlegen.

## Dateien
| Datei | Inhalt | Flash-Offset |
|---|---|---|
| `NHR-heltec_v4_repeater-factory.bin` | Komplettimage (Bootloader + Partitionen + App) | `0x0` |
| `NHR-heltec_v4_repeater-firmware.bin` | nur App-Image (Update bei bereits vorhandenem Bootloader) | `0x10000` |

SHA-256-Prüfsummen: siehe `shasum -a 256 *.bin`.

## Variante A — MeshCore Web-Flasher (einfachste)
1. https://flasher.meshcore.co.uk öffnen → „Custom firmware".
2. `NHR-heltec_v4_repeater-firmware.bin` wählen, Gerät per USB verbinden, flashen.

## Variante B — esptool (Komplettimage @ 0x0)
```bash
python -m esptool --chip esp32s3 write_flash 0x0 NHR-heltec_v4_repeater-factory.bin
```
Nur App aktualisieren (Bootloader/Partitionen bleiben):
```bash
python -m esptool --chip esp32s3 write_flash 0x10000 NHR-heltec_v4_repeater-firmware.bin
```

## Nach dem Flashen — NHR-Parameter (alle reversibel, ohne Reflash)
| Befehl | Wirkung | Upstream-Verhalten |
|---|---|---|
| `set rxdelay 0` | RX-SNR-Flutung aus (Phase 0) | `0.0` |
| `set txsnrweight 0` | TX-SNR-Flutung aus (Phase 1) | `0.0` |
| `get txsnrweight` | aktuellen Wert lesen | — |
| `set flood.max 10` | Hop-Limit an Netz-Durchmesser anpassen | `64` |

NHR-Defaults: `rxdelay 10`, `txsnrweight 0.5`. Zum vollständigen Zurücksetzen Upstream-Firmware flashen.
