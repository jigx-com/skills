# adb Recipes for Jigx Mobile Testing

The "hands" of the rig. Pair every adb action with a `track_logs` / `stop_collecting` window and
a device-DB verification, per the closed-loop procedure in `SKILL.md`. Replace `$PKG` with the
package name from `solution-model.md`.

---

## Mechanics (get these right or everything flakes)

### Locate an element — never hardcode coordinates

```bash
adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml -
```

Parse the XML, find the node by `text`, `content-desc`, or `resource-id`, read its `bounds`
(`[x1,y1][x2,y2]`), and tap the centre: `x = (x1+x2)/2`, `y = (y1+y2)/2`. Coordinates change with
scroll position and keyboard state, so re-dump before each tap rather than reusing a point.
Record the stable locator (the attribute that survives re-renders) in the selector map.

### Tap / type / swipe

```bash
adb shell input tap <x> <y>
adb shell input text "field%svalue"          # %s is a literal space; escape special chars
adb shell input swipe <x1> <y1> <x2> <y2> 300 # last arg = duration ms
```

### Soft keyboard + scroll-to-element

Tapping a field below the fold or behind the keyboard misses. Before tapping a field:
dismiss the keyboard if open (`adb shell input keyevent 111` = ESC/back-dismiss), or swipe the
form up until the target's `bounds` sit in the visible area above the keyboard. Confirm
visibility from a fresh `uiautomator dump` before tapping.

### System dialogs live outside the Jigx layer

Camera, file picker, and permission dialogs are OS UI — the Jigx logs won't show them and the
in-app selector map won't contain them. Handle them with their own `uiautomator dump` +
`resource-id` taps (e.g. `com.android.packageinstaller`/`com.google.android.documentsui` nodes).
Record the exact dialog-handling sequence per device/OS version in the solution model, because it
differs across Android versions. Relevant to photo upload and PDF-attachment flows.

### Wait on a condition, not a clock

Fixed `sleep` is the top flake source. Poll instead:

```bash
# poll until the ready-signal element appears (timeout guard)
for i in $(seq 1 20); do
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  adb pull /sdcard/ui.xml - 2>/dev/null | grep -q 'READY_SIGNAL' && break
  sleep 0.5
done
```

Or close the log window and confirm the completion event landed before proceeding. Each screen's
ready-signal lives in `solution-model.md`.

### App lifecycle

```bash
adb shell am force-stop $PKG          # clean kill (used for draft-rehydration test)
adb shell monkey -p $PKG -c android.intent.category.LAUNCHER 1   # relaunch
adb shell pm clear $PKG               # full reset (DESTRUCTIVE — see wipe guardrail)
```

### Network control

```bash
adb shell svc wifi disable && adb shell svc data disable   # go offline
adb shell svc wifi enable  && adb shell svc data enable    # back online
```

---

## Scenario recipes

Each recipe is a sequence of closed-loop steps. Assert at every phase; the power of offline-first
testing is that each phase is independently verifiable in the DB.

### Offline submit + sync

1. Go offline (`svc wifi disable` + `svc data disable`).
2. `track_logs` → fill + submit the form via the locate/tap/type loop → wait on the
   "saved locally" ready-signal → `stop_collecting`.
3. **Verify (offline):** `query_device_db` the form's table → the row exists with a *pending /
   unsynced* status. If it's absent, the offline write failed.
4. Go back online (`svc ... enable`).
5. `track_logs` → wait for the sync/upload completion event in the window → `stop_collecting`.
6. **Verify (synced):** the same row's status flips to synced, and the Acumatica write lands
   (check the field/GI the integration targets). A local flip without the remote write is a
   half-failure worth catching.

### Draft rehydration / `isDirty`

1. `track_logs` → fill the form *partially* → trigger the draft-save path → `stop_collecting`.
2. **Verify:** the draft is written to **LOCAL** storage (not DYNAMIC) — this is the exact
   storage-layer question the investigation skill checks.
3. `adb shell am force-stop $PKG` then relaunch.
4. Reopen the form → assert it rehydrates with the partial values intact. Missing values =
   either wrong storage layer on write, or reading the wrong layer on open.

### Upload progress / retry

1. Start an attachment upload (photo/PDF) via the file-picker dialog handling above.
2. Mid-upload, drop the network (`svc wifi disable`).
3. **Verify:** UI shows a retry/pending state, and the DB/staging reflects an incomplete upload
   rather than a phantom success.
4. Restore network → assert the upload resumes/retries and completes, with the staged local image
   present in the generated PDF where applicable.

### Multi-tenant routing

For each tenant in the org→tenant map:

1. `select_debug_organization(<orgId>)` for the tenant.
2. Run the form's happy-path submit (offline-submit recipe is fine).
3. **Verify:** the record routes to the correct tenant — confirm via the DB and the
   tenant-specific Acumatica target. The historical failure mode here was a published-customization
   change breaking a single tenant (the Tanzania incident), so assert per-tenant rather than
   assuming one passing tenant covers the rest.
