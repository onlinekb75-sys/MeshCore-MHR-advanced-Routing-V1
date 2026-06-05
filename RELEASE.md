# Releasing Firmware

GitHub Actions is set up to automatically build and release firmware.

It will automatically build firmware when one of the following tag formats are pushed.

- `companion-v1.0.0`
- `repeater-v1.0.0`
- `room-server-v1.0.0`

> NOTE: replace `v1.0.0` with the version you want to release as.

- You can push one, or more tags on the same commit, and they will all build separately.
- Once the firmware has been built, a new (draft) GitHub Release will be created.
- You will need to update the release notes, and publish it.

---
## 🇩🇪 Deutsche Übersetzung

# Firmware veröffentlichen

GitHub Actions ist so eingerichtet, dass Firmware automatisch gebaut und veröffentlicht wird.

Die Firmware wird automatisch gebaut, wenn eines der folgenden Tag-Formate gepusht wird.

- `companion-v1.0.0`
- `repeater-v1.0.0`
- `room-server-v1.0.0`

> HINWEIS: Ersetze `v1.0.0` durch die Version, die du veröffentlichen möchtest.

- Du kannst einen oder mehrere Tags auf denselben Commit pushen — sie werden alle separat gebaut.
- Sobald die Firmware gebaut wurde, wird ein neues (Entwurfs-)GitHub-Release erstellt.
- Du musst die Release-Notizen aktualisieren und das Release veröffentlichen.
