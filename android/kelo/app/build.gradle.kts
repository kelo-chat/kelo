import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.baselineprofile)
}

// Push (Firebase Cloud Messaging) needs app/google-services.json (package chat.kelo.app). The file is tracked with the source on
// purpose: it is the Firebase CLIENT config (project id, app id, a client API key), which is public because it is inside
// every APK, and the published source has to build the same APK as the store's. To build with your own Firebase project, replace the
// file with yours (app/google-services.json.example shows its shape; a service-account key never belongs here). Without any file the
// build still works, but says so clearly, and the app runs without push: notifications then come from the 15-minute check and from
// following a reply in the background (docs/verify-builds.md).
if (file("google-services.json").exists()) {
    apply(plugin = libs.plugins.google.services.get().pluginId)
} else {
    logger.warn("Kelo: app/google-services.json is missing - this build has NO push notifications (FCM), and it will not match the store build. Add the file to enable them.")
}

// Release signing: android/keystore/keystore.properties (kept out of git). Without it, a release build is left unsigned.
val keystoreDir = rootProject.file("../keystore")
val keystoreProps = Properties().apply {
    val f = File(keystoreDir, "keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "chat.kelo.app"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "chat.kelo.app"
        minSdk = 26
        targetSdk = 36
        // every release gets a higher number, so the phone always treats a new APK as an update and the My page shows which build runs.
        // The number comes from ONE tracked place, never from the clock of the build machine, so the same source gives the same APK:
        //   1. -PkeloBuild=N         (tools/build/android.sh passes it; --build N rebuilds a given version)
        //   2. version.properties    (build=N, next to settings.gradle.kts; tools/build/android.sh writes it when the owner builds a
        //                             release, and it is committed with that release, so a rebuild from the tag gives the same number)
        //   3. the minutes rule      (minutes since late 2026) only when there is no version.properties at all (a bare copy of the code)
        // tools/build/android.sh reads the minutes formula below to work out N for a fresh release; nothing else here reads the clock.
        val versionFile = rootProject.file("version.properties")
        val fromFile = if (versionFile.exists()) Properties().apply { versionFile.inputStream().use { load(it) } }.getProperty("build") else null
        val build = (project.findProperty("keloBuild") as String?)?.toInt()
            ?: fromFile?.trim()?.toInt()
            ?: ((System.currentTimeMillis() / 60_000L) - 29_500_000L).toInt()   // minutes since late 2026
        versionCode = build
        // the release the people see comes from the one root version.json (docs/versioning.md): versionName = "version" (0.9.0);
        // versionCode stays the store's rising number above. min_server: the oldest Kelo server this build works with.
        val release = groovy.json.JsonSlurper().parse(rootProject.file("../../version.json")) as Map<*, *>
        versionName = release["version"] as String
        buildConfigField("String", "MIN_SERVER", "\"${release["min_server"]}\"")
        // where the builds handed out as a file look for a newer version (update/UpdateRules.kt); a Play build never looks
        buildConfigField("String", "UPDATE_BASE", "\"https://kelo.chat\"")
        // Google Pay's card recognition (wallet/CardScan.kt): the test environment on debug builds, production on release builds
        // (production opens only once the app is registered in the Google Pay & Wallet Console; until then the camera scan is used)
        buildConfigField("boolean", "CARD_SCAN_TEST", "false")
    }

    signingConfigs {
        if (!keystoreProps.isEmpty) {
            create("release") {
                storeFile = File(keystoreDir, keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    // Three builds from the same code (BuildConfig.FULL_DEVICE says which 이 기기에서 rows exist):
    //  - "sideload" (default; the APK handed to users as a file, android/dist/kelo-0.1.0.apk) and "play" (the Google Play bundle)
    //    leave out text messages, the call log and notification reading. Google Play allows READ_SMS, SEND_SMS and READ_CALL_LOG only
    //    for the default SMS/phone/assistant app or an approved exception, and Play Protect blocks apps installed from a browser,
    //    messenger or file manager that ask for SMS permissions or notification access.
    //  - "full": everything (src/full/AndroidManifest.xml adds those permissions and the notification listener). For installs with
    //    adb, or a later Play build once the declarations are approved; Play Protect blocks it when it is sideloaded from a file.
    // ./gradlew assembleSideloadRelease -> app/build/outputs/apk/sideload/release/app-sideload-release.apk
    // ./gradlew bundlePlayRelease       -> app/build/outputs/bundle/playRelease/app-play-release.aab
    // ./gradlew assembleFullRelease     -> app/build/outputs/apk/full/release/app-full-release.apk
    flavorDimensions += "store"
    productFlavors {
        create("sideload") {
            dimension = "store"
            isDefault = true
            buildConfigField("boolean", "FULL_DEVICE", "false")
            buildConfigField("boolean", "HEALTH", "true")
        }
        create("play") {
            dimension = "store"
            buildConfigField("boolean", "FULL_DEVICE", "false")
            // Health Connect is left out of the first Play release (its own health-data review); src/play/AndroidManifest.xml
            // removes its permissions, query and rationale screens
            buildConfigField("boolean", "HEALTH", "false")
        }
        create("full") {
            dimension = "store"
            buildConfigField("boolean", "FULL_DEVICE", "true")
            buildConfigField("boolean", "HEALTH", "true")
        }
    }

    // The Play bundle carries a list of the libraries inside (BUNDLE-METADATA, the same on every build). The file builds do not:
    // an APK's copy is encrypted for Google with a fresh random value on every build, so two builds of the same source would differ.
    dependenciesInfo {
        includeInApk = false
        includeInBundle = true
    }

    buildTypes {
        debug {
            buildConfigField("boolean", "CARD_SCAN_TEST", "true")
            // tests only: ./gradlew installSideloadDebug -PkeloUpdateBase=http://10.0.2.2:8931 reads a local mock's latest.json
            // (release builds always use https://kelo.chat)
            (project.findProperty("keloUpdateBase") as String?)?.let { buildConfigField("String", "UPDATE_BASE", "\"$it\"") }
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
    // BouncyCastle and jspecify both carry an OSGi manifest for Java 9+ at the same path; the app needs neither
    packaging {
        resources {
            excludes += "META-INF/versions/9/OSGI-INF/MANIFEST.MF"
        }
    }
    // the update code of the builds handed out as a file (update/AppUpdater.kt behind AppUpdatesFactory); the Play build has its
    // own in src/play (Google Play's In-App Updates)
    sourceSets {
        getByName("sideload").java.srcDir("src/fileBuilds/java")
        getByName("full").java.srcDir("src/fileBuilds/java")
    }
}

// Reproducible builds: R8 writes how long it ran and with how many threads into the metadata that goes into the Play bundle
// (BUNDLE-METADATA/com.android.tools/r8.json). That is the only thing in the bundle that changes from build to build of the same
// source, so the two numbers are blanked; every other field (options, dex checksum, version) stays. Nothing reads them.
tasks.configureEach {
    if (name.startsWith("minify") && name.endsWith("ReleaseWithR8")) {
        doLast {
            outputs.files.asFileTree.matching { include("**/r8-metadata.dat") }.forEach { f ->
                f.writeText(f.readText().replace(Regex("\"compilation\":\\{[^}]*\\}"), "\"compilation\":{}"))
            }
        }
    }
}

// The baseline profile is generated on a phone or emulator that is signed in to a Kelo server:
//   ./gradlew :app:generateReleaseBaselineProfile -Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true
// (leaving the apps installed keeps the sign-in). It is written to app/src/release/generated/baselineProfiles and used by
// assembleRelease from then on; generation is not part of a normal build.
baselineProfile {
    automaticGenerationDuringBuild = false
}

// ./gradlew :app:assembleRelease -PcomposeReports writes the Compose compiler's stability reports to app/build/compose_reports
composeCompiler {
    if (project.hasProperty("composeReports")) {
        reportsDestination = layout.buildDirectory.dir("compose_reports")
        metricsDestination = layout.buildDirectory.dir("compose_reports")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.profileinstaller)
    implementation(libs.androidx.splashscreen)
    implementation(libs.androidx.biometric)
    implementation(libs.androidx.fragment)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)
    // KE2E, the end-to-end encryption to the user's own server (core/e2e, docs/security/e2e-and-phone-key.md): BouncyCastle's
    // lightweight API (HPKE with export, ChaCha20-Poly1305), pure Java, so it works the same on every Android version (minSdk 26)
    implementation(libs.bouncycastle.prov)
    implementation(libs.camerax.camera2)
    implementation(libs.camerax.lifecycle)
    implementation(libs.camerax.view)
    implementation(libs.mlkit.barcode)
    implementation(libs.zxing.core)
    // 카드 스캔 (wallet/CardScan.kt), in every build: Google Pay's card recognition, else the camera with ML Kit's text recognizer that
    // Google Play services provides (the bundled recognizer would add about 11 MB per CPU type)
    implementation(libs.play.services.wallet)
    implementation(libs.mlkit.text.recognition)
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)
    implementation(libs.health.connect)
    // photos, videos and sound in My documents: played in the app, streamed from the server with the bearer token (Range requests)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.ui)
    implementation(libs.media3.okhttp)
    // Health Connect brings Guava at run time, which replaces the small ListenableFuture library CameraX compiles against: keep it on
    // the compile classpath too (R8 strips what is unused)
    implementation(libs.guava)
    // Google Play's In-App Updates: the Play build only (src/play/.../update/PlayAppUpdates.kt); the APKs handed out as a file never
    // carry Play Core (UpdateDependencyTest in src/testSideload checks)
    "playImplementation"(libs.play.app.update)
    "playImplementation"(libs.play.app.update.ktx)
    debugImplementation(libs.compose.ui.tooling)
    // the generator journey (launch, chat, connectors, My) that writes src/release/generated/baselineProfiles
    baselineProfile(project(":baselineprofile"))

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    // PlayAppUpdatesTest: Play Core's FakeAppUpdateManager needs PendingIntent.getBroadcast to return something (the plain JVM
    // android.jar returns null), so that one static call is stubbed
    "testPlayImplementation"(libs.mockito.core)
}

// With several builds there is no plain testDebugUnitTest any more; this keeps `./gradlew testDebugUnitTest` working.
tasks.register("testDebugUnitTest") {
    group = "verification"
    description = "Runs the unit tests of the sideload and full debug builds."
    dependsOn("testSideloadDebugUnitTest", "testFullDebugUnitTest")
}
