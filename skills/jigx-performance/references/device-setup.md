# Device and tool setup for timing

Read this when `preflight.py` lists setup steps, or when a simulator, emulator, Maestro
or adb command fails. The audit needs none of this; timing needs a device with the Jigx
app and the test user signed in.

## How to guide the user

- Work through `preflight.py`'s numbered steps **one at a time**, in its order. Say
  what the step is for in one line ("Maestro taps through the screens and times them").
- **Ask before running anything that installs, downloads or boots** (`brew install`,
  `sdkmanager`, `xcodebuild -downloadPlatform`, `simctl boot`, `emulator -avd`). Run it
  when the user agrees; downloads are large (an iOS runtime or an Android image is
  several GB), so say so and run them in the background.
- **Never run `sudo` yourself.** Show the command and ask the user to type it with a
  leading `!` (for example `! sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`)
  so the password prompt reaches them.
- Steps that need a window (Xcode from the App Store, Android Studio's first-run wizard,
  signing in to the Jigx app, accepting a USB debugging prompt): say exactly what to
  click and wait for "done".
- After each step, rerun `preflight.py` and show the new list. When a command fails,
  match its error in "Problems and fixes" below before trying anything else; after two
  failed attempts at the same step, stop and ask.
- The user only times iOS? Android steps can be skipped; the report says Android was not
  measured. Same for any tool the user declines.

## iOS Simulator (macOS only)

1. **Xcode.** Install from the App Store (about 15 GB with a runtime), open it once and
   accept the components it offers. If `xcode-select -p` does not print a path inside
   `Xcode.app`, the user runs:
   `! sudo xcode-select -s /Applications/Xcode.app/Contents/Developer && sudo xcodebuild -runFirstLaunch`.
   A license error: `! sudo xcodebuild -license accept`.
2. **A runtime.** `xcrun simctl list runtimes` empty: `xcodebuild -downloadPlatform iOS`.
3. **A simulator.** Boot an existing iPhone: `xcrun simctl boot <udid>` (preflight
   names one). None exists: `xcrun simctl create "iPhone for Jigx" "iPhone 17 Pro"`
   prints the new udid. To see it: in Xcode 27 the simulator window is DeviceHub,
   `open /Applications/Xcode.app/Contents/Applications/DeviceHub.app` (Simulator.app is gone;
   on Xcode 26 and older, `open -a Simulator`).
4. **The Jigx app.** The App Store does not exist on a simulator: it needs the
   **simulator build** (`.app`, not an `.ipa`) of the Jigx app or the customer's branded
   app, which the user should already have. Ask where it is. Install it with
   `xcrun simctl install <udid> <path>.app` (unzip a `.zip` first), open it and sign in
   with the **test** user. Preflight then shows `com.jigx.…` next to the simulator.

## Android emulator

1. **The SDK.** Easiest: `brew install --cask android-studio`, open it once and finish
   the setup wizard; it installs the SDK in `~/Library/Android/sdk`. Then put the tools
   on the path for this shell (and in `~/.zshrc` if the user wants it to stay):

   ```sh
   export ANDROID_HOME=~/Library/Android/sdk
   export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
   ```

   No `cmdline-tools/latest`: Android Studio > Settings > Languages & Frameworks > Android
   SDK > SDK Tools > tick "Android SDK Command-line Tools".
2. **A Google APIs image, not Google Play.** Sync timing reads the app's database with
   `adb root`, and Google Play images refuse it ("adbd cannot run as root in production
   builds"). Apple silicon uses `arm64-v8a`, Intel `x86_64`:

   ```sh
   sdkmanager "platform-tools" "emulator" "system-images;android-34;google_apis;arm64-v8a"
   avdmanager create avd -n jigx-api34 -k "system-images;android-34;google_apis;arm64-v8a" -d pixel_6
   ```

   Or in Android Studio: Device Manager > Create device > Pixel 6 > the "Google APIs"
   image (its target does not say "Google Play"). A Google Play emulator is still fine for
   screen timing.
3. **Start it.** `emulator -avd jigx-api34 -no-snapshot-load &`, then wait until
   `adb shell getprop sys.boot_completed` prints `1`. One emulator at a time.
4. **The Jigx app.** A Google APIs image has no Play Store. The user should already have
   an **APK** or an **AAB** of the Jigx app or the branded app; ask where it is.
   - An APK: `adb install <jigx>.apk`.
   - An AAB (an app bundle cannot be installed directly): convert it with bundletool
     (`brew install bundletool`) into one universal APK, then install that:

     ```sh
     bundletool build-apks --bundle=<jigx>.aab --output=jigx.apks --mode=universal
     unzip -o jigx.apks universal.apk && adb install universal.apk
     ```

     bundletool signs with the Android debug key (`~/.android/debug.keystore`, made by
     Android Studio or the first emulator start); with none, add
     `--ks=<keystore> --ks-key-alias=<alias>`. Because the signature differs from the
     store build, uninstall any store-signed Jigx app on that emulator first.
   - Neither: on a Google Play emulator or a phone that has the app from the Play Store,
     `adb shell pm path com.jigx.<app>` gives the path (several lines for a split app),
     `adb pull <path>` each, then `adb install-multiple base.apk split_*.apk` on the Google
     APIs emulator.

   Open the app and sign in with the test user.
5. **A phone instead** (screens only; its app files are not reachable for sync timing):
   Settings > About phone > tap Build number 7 times; Developer options > USB debugging on;
   plug it in and accept the prompt. `adb devices` must say `device`, not `unauthorized`.

## Tools

| Tool | For | Install | Check |
|---|---|---|---|
| Homebrew | the installs below | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` (asks for the password: the user runs it with `!`) | `brew --version` |
| Node 22.5+ | query timing (`node:sqlite`) | `brew install node@22`, or `nvm install 22` when they use nvm | `node --version` |
| Java 17+ | Maestro | `brew install openjdk@17`. Nothing else: `time-screens.py` runs Maestro with an installed Java 17+ even when another Java is active (JAVA_HOME for its process only). Do not change `JAVA_HOME` in `~/.zshrc`: the user may pin another Java on purpose. To run `maestro` by hand: `JAVA_HOME="$(brew --prefix)/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home" maestro …` | `preflight.py` (its Java line) |
| Maestro | screen and cold-start timing | `brew install mobile-dev-inc/tap/maestro` (the `curl … get.maestro.mobile.dev` script is often blocked) | `maestro --version` |
| `jigx_debug` MCP | SDK projects: publishing and the device database | the Jigx MCP setup for Claude Code | `/mcp` lists `jigx_debug` |

## Problems and fixes

| Error or symptom | Cause | Fix |
|---|---|---|
| `xcrun: error: unable to find utility "simctl"` | Command Line Tools selected, not Xcode | `! sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` |
| `Unable to boot device in current state: Booted` | already booted | nothing to do; open DeviceHub to see it |
| `simctl boot` hangs or the simulator stays black | stale simulator service | `xcrun simctl shutdown all`, `killall -9 com.apple.CoreSimulator.CoreSimulatorService`, boot again |
| `No devices are booted` / Maestro "No connected devices" | nothing booted, or Maestro started before boot finished | boot, wait for the home screen, run again |
| `simctl install` "Failed to install the app" / wrong architecture | an `.ipa` or a device build | it needs the simulator build (`.app`) |
| Maestro `Unable to locate a Java Runtime` or `UnsupportedClassVersionError` | no Java, or Java older than 17 (macOS's `/usr/bin/java` is only a stub) | install Java 17 (Tools); `time-screens.py` finds it. Running `maestro` by hand: set `JAVA_HOME` for that command only |
| Maestro stalls on the first iOS run | it builds and installs its XCTest driver once | wait up to two minutes; later runs are quick |
| `adb: command not found` | platform-tools not on the path | the `export` lines under Android step 1 |
| `adb devices` shows `unauthorized` | phone prompt not accepted | accept "Allow USB debugging" on the phone; replug |
| `adb devices` shows `offline` | adb server out of step | `adb kill-server && adb start-server`; an emulator: close it, `emulator -avd <name> -no-snapshot-load` |
| `adbd cannot run as root in production builds` | Google Play image | create and use a Google APIs emulator (Android step 2) |
| emulator `PANIC: Missing emulator engine program` or `PANIC: Broken AVD system path` | emulator package or system image missing | `sdkmanager "emulator" "<the image>"` |
| emulator very slow or "x86 emulation currently requires hardware acceleration" | an `x86_64` image on Apple silicon | use the `arm64-v8a` image |
| emulator never finishes booting | broken snapshot | `emulator -avd <name> -wipe-data` (signs the app out) |
| `adb install` of an `.aab` fails, or "Invalid APK file" | an app bundle, not an APK | convert it with bundletool (Android step 4) |
| bundletool `Keystore file … not found` | no Android debug key yet | start an emulator once, or pass `--ks` and `--ks-key-alias` |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | APK built for another architecture | an APK for the emulator's ABI, or a universal APK |
| `INSTALL_FAILED_MISSING_SPLIT` | a split app installed from one APK | pull every split and use `adb install-multiple` |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | an app with another signature is installed | `adb uninstall com.jigx.<app>`, then install |
| `sdkmanager` Java errors (`NoClassDefFoundError`, wrong class file version) | old `tools/bin/sdkmanager`, or Java too old | use `cmdline-tools/latest/bin/sdkmanager` with Java 17 |
