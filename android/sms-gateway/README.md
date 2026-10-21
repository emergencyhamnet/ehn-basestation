# EHN Relay Link

EHN Relay Link is the Android phone gateway for the EHN basestation. It forwards accepted
carrier SMS and notification-derived Google Messages RCS ingress to the basestation, polls for
outbound SMS work, sends using the phone's SMS service, and reports the local send completion.
The basestation remains responsible for the directory, authorization, and routing policy.

This is alpha software. The debug APK is not a production-signed release. Keep gateway tokens
private and use a trusted network or the documented USB/ADB development link.

## Build

Requirements: Android Studio/Android SDK with API 34 installed, JDK 17, and an internet
connection for Gradle dependencies. Set `JAVA_HOME` to JDK 17 if it is not already configured.
Do not commit `local.properties`, which contains a machine-specific Android SDK path.

From this directory:

```powershell
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug --no-daemon --console=plain
```

```bash
./gradlew :app:testDebugUnitTest :app:assembleDebug --no-daemon --console=plain
```

The debug APK is written under `app/build/outputs/apk/debug/`. Generated build output is not
part of the source repository. For installation, permissions, the Android/basestation API
contract, and USB/ADB setup, see the [Android installation guide](../../dev/windows/android-relay-installation.md).

## Included Sources

The app source, resources, manifest, and unit tests are under `app/src/`. Gradle build files and
the Gradle wrapper are included so the project can be built without a separately installed
Gradle command. The wrapper files retain their upstream Apache-2.0 notices.

Do not add phone logs, local SDK configuration, generated APKs, signing keys, tokens, or user
data to commits.