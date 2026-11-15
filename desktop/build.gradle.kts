import java.util.Properties
import org.jetbrains.compose.desktop.application.dsl.TargetFormat
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

// Kelo for the desktop (Windows first; macOS and Linux build from the same code). Compose Multiplatform for Desktop.
//
//   ./gradlew run                      the app (JAVA_HOME = a JDK 17)
//   ./gradlew test                     the unit tests (shared rules and the desktop's own)
//   ./gradlew packageDmg               a macOS disk image (on a Mac)
//   ./gradlew packageMsi packageExe    Windows installers (only on Windows; see .github/workflows/desktop-windows.yml)
//   ./gradlew createDistributable      the app folder the MSIX for the Microsoft Store is packed from (packaging/windows/)
//
// The Android app (android/kelo) is the reference. Its plain Kotlin files (the API client, the models, the rules) and the
// Compose files that need nothing from Android (the character, the icons) are not copied by hand: [syncShared] takes them
// from android/kelo at build time, so both apps always use the same code. Nothing in android/kelo is changed by this build.
// The text comes from the Android strings too (tools/sync_strings.py writes src/main/resources/i18n and src/generated).

plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21"
    id("org.jetbrains.compose") version "1.7.3"
}

group = "chat.kelo"
/**
 * Two numbers (docs/versioning.md):
 * - the release people see, `version` in the repository's version.json (e.g. 0.9.0), with `build` and `min_server`, compiled
 *   into chat.kelo.desktop.core.ReleaseInfo (shown in My, sent as X-Kelo-App, compared with the server's X-Kelo-Server);
 * - the installers' package number, `packageVersion` in desktop/version.properties (1.0.8, ...): MSI needs MAJOR.MINOR.BUILD
 *   with MAJOR >= 1, macOS too, and an update needs a higher one; the MSIX adds a fourth part (packaging/windows/make_msix.ps1).
 *   `-PkeloVersion=X.Y.Z` overrides it for a one-off build only.
 */
val releaseInfo: Map<String, Any?> = run {
    @Suppress("UNCHECKED_CAST")
    groovy.json.JsonSlurper().parse(rootProject.file("../version.json")) as Map<String, Any?>
}
val releaseVersion = releaseInfo["version"] as String
val releaseBuild = (releaseInfo["build"] as Number).toInt()
val minServer = releaseInfo["min_server"] as String
val packageProps = Properties().apply { rootProject.file("version.properties").inputStream().use { load(it) } }
val keloVersion = (findProperty("keloVersion") as String?) ?: packageProps.getProperty("packageVersion")
version = keloVersion

val androidSrc = rootProject.file("../android/kelo/app/src/main/java")
val androidTest = rootProject.file("../android/kelo/app/src/test/java")

/** Android files used exactly as they are (relative to android/kelo/app/src/main/java/chat/kelo/app). */
val sharedFiles = listOf(
    "core/AcceptLanguage.kt", "core/ApiError.kt", "core/KeloApi.kt", "core/Languages.kt", "core/Models.kt", "core/Pairing.kt",
    "core/SecureStore.kt", "core/Sessions.kt", "core/ScreenCache.kt", "core/WholeNumbers.kt",
    "chat/ChatModels.kt", "chat/TurnEvents.kt", "chat/TurnFollower.kt", "chat/ChatItems.kt", "chat/Markdown.kt",
    "chat/MessageActions.kt", "chat/AgentActivity.kt", "chat/QuickReplyRules.kt", "chat/ReplyFields.kt", "chat/ModelChooserRules.kt", "chat/vault/VaultModels.kt",
    "ui/character/KeloCharacter.kt", "ui/character/KeloArt.kt", "ui/icons/KeloIcons.kt",
    "followup/FollowupModels.kt",
    "connectors/ConnectorModels.kt", "goals/GoalModels.kt", "goals/GoalMeta.kt",
    "my/MyModels.kt", "my/UsageLogic.kt", "my/NumberText.kt", "my/CliUpdate.kt", "my/ServerUpdate.kt", "my/ContactHours.kt",
    "agent/AgentModels.kt", "agent/AgentRules.kt", "agent/LiveRules.kt", "agent/SkillModels.kt", "agent/TaskGrantModels.kt", "agent/ScheduleGrantModels.kt",
    "work/WorkRules.kt", "notify/NotifyRules.kt",
    "chat/ChatAsks.kt", "chat/ChatScroll.kt", "chat/TimeLabels.kt", "chat/Reactions.kt", "ui/components/TokenText.kt",
    "wallet/CardBlob.kt", "wallet/CardRules.kt", "wallet/Money.kt", "wallet/WalletLock.kt", "wallet/WalletModels.kt", "wallet/ShippingModels.kt",
    "chat/SecretGuard.kt", "core/e2e/Ke2e.kt", "core/e2e/Ke2eRequest.kt", "core/e2e/Ke2eFrames.kt",
    "core/e2e/Ke2eHttp.kt", "core/e2e/Ke2eWebSocket.kt", "core/AppVersion.kt", "keyguard/KeyguardModels.kt", "chat/AiConsent.kt", "my/StorageRules.kt", "my/ToneRules.kt", "chat/PaymentStepCalls.kt", "chat/ChatOverlayClear.kt",
)

/** Android unit tests of those files, run here too (they need nothing from Android). */
val sharedTests = listOf(
    "core/PairingLinksTest.kt", "core/LanguagesTest.kt", "core/AcceptLanguageTest.kt", "core/KeloApiTest.kt", "core/SecureStoreTest.kt",
    "chat/TurnEventParseTest.kt", "chat/TurnFollowerTest.kt", "chat/ChatReducerTest.kt", "chat/MarkdownTest.kt",
    "chat/MessageActionsTest.kt", "chat/ModelChooserRulesTest.kt", "chat/PurchaseEventsTest.kt",
    "my/UsageLogicTest.kt", "my/NumberTextTest.kt", "my/ResourcesModelsTest.kt", "my/CliUpdateTest.kt", "my/ServerUpdateTest.kt", "my/ContactHoursRulesTest.kt", "goals/GoalMetaTest.kt",
    "work/WorkRulesTest.kt", "ui/character/KeloMotionTest.kt", "agent/LiveRulesTest.kt", "agent/MemoryPassRulesTest.kt",
    "agent/SkillModelsTest.kt",
    "chat/ChatAsksTest.kt", "chat/ChatScrollTest.kt",
    "chat/SignOutRulesTest.kt", "chat/SignInRulesTest.kt", "chat/ModelRulesTest.kt", "chat/ReportRulesTest.kt",
    "my/OperatorRulesTest.kt", "wallet/CardRulesTest.kt", "wallet/CardBlobTest.kt", "connectors/BetaRulesTest.kt",
    "goals/GoalsStateTest.kt", "connectors/ConnectorRulesTest.kt", "connectors/ConnectorListTest.kt", "connectors/GoogleReviewRulesTest.kt",
    "connectors/ConnectorIconRulesTest.kt", "chat/QuickReplyRulesTest.kt", "chat/ReplyFieldsTest.kt", "goals/GoalActivityTest.kt", "chat/TimeLabelsTest.kt", "chat/ReactionsTest.kt",
    "wallet/AutoStateTest.kt", "wallet/ShippingModelsTest.kt", "chat/ShippingEventsTest.kt", "chat/SecretGuardTest.kt", "chat/SecretRequestsTest.kt", "chat/HandoverTest.kt",
    "core/e2e/Ke2eEnvelopeTest.kt", "core/e2e/Ke2eRfc9180Test.kt", "core/e2e/Ke2eVectorsFileTest.kt", "core/e2e/Ke2eStreamingTest.kt",
    "core/AppVersionTest.kt", "core/e2e/Ke2eHttpTest.kt", "core/e2e/Ke2eLocalServerTest.kt", "keyguard/KeyguardRulesTest.kt", "chat/AiConsentTest.kt", "chat/AgentActivityTest.kt", "my/StorageRulesTest.kt",
    "agent/TaskGrantCardTest.kt", "chat/PaymentStepTest.kt", "chat/ToolFoldTest.kt", "chat/BriefingCardTest.kt",
    "my/BriefingSettingsRulesTest.kt", "my/BriefingSourcesRulesTest.kt", "chat/ChatOverlayRulesTest.kt",
    "my/ToneRulesTest.kt", "ui/components/FieldWrapScan.kt",
)

val sharedDir = layout.buildDirectory.dir("generated/shared/kotlin")
val sharedTestDir = layout.buildDirectory.dir("generated/sharedTest/kotlin")

val syncShared by tasks.registering(Sync::class) {
    description = "Copies the Android app's plain Kotlin files the desktop app shares (see sharedFiles)."
    from(androidSrc) { sharedFiles.forEach { include("chat/kelo/app/$it") } }
    into(sharedDir)
    doLast {
        val missing = sharedFiles.filterNot { File(androidSrc, "chat/kelo/app/$it").exists() }
        check(missing.isEmpty()) { "android/kelo no longer has: $missing (update sharedFiles in desktop/build.gradle.kts)" }
    }
}

val syncSharedTests by tasks.registering(Sync::class) {
    description = "Copies the Android unit tests of the shared files (see sharedTests); the ones that are missing are skipped."
    from(androidTest) { sharedTests.forEach { include("chat/kelo/app/$it") } }
    into(sharedTestDir)
}

val appVersionDir = layout.buildDirectory.dir("generated/appVersion/kotlin")
val generateAppVersion by tasks.registering {
    description = "Writes chat.kelo.desktop.core.ReleaseInfo from ../version.json and version.properties."
    val out = appVersionDir
    val values = listOf(releaseVersion, releaseBuild.toString(), minServer, keloVersion)
    inputs.property("values", values)
    outputs.dir(out)
    doLast {
        val f = out.get().file("chat/kelo/desktop/core/ReleaseInfo.kt").asFile
        f.parentFile.mkdirs()
        f.writeText(
            """
            |package chat.kelo.desktop.core
            |
            |/** Generated by build.gradle.kts from version.json and desktop/version.properties (docs/versioning.md). Do not edit. */
            |object ReleaseInfo {
            |    /** The release people see (version.json `version`) */
            |    const val VERSION = "${values[0]}"
            |    /** version.json `build` */
            |    const val BUILD = ${values[1]}
            |    /** The oldest server release this app works with (version.json `min_server`) */
            |    const val MIN_SERVER = "${values[2]}"
            |    /** The installers' package number (desktop/version.properties), what /download/latest.json compares */
            |    const val PACKAGE = "${values[3]}"
            |}
            |""".trimMargin(),
        )
    }
}

sourceSets {
    main {
        kotlin.srcDir(sharedDir)
        kotlin.srcDir(appVersionDir)
        kotlin.srcDir("src/generated/kotlin")
    }
    test {
        kotlin.srcDir(sharedTestDir)
    }
}

tasks.named("compileKotlin") { dependsOn(syncShared, generateAppVersion) }
tasks.named("compileTestKotlin") { dependsOn(syncSharedTests) }

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
        freeCompilerArgs.add("-Xjvm-default=all")
    }
}
java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

dependencies {
    implementation(compose.desktop.currentOs)
    implementation(compose.material3)
    implementation(compose.foundation)
    implementation(compose.ui)
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-swing:1.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Windows DPAPI (the saved sign-in is sealed to the Windows user) and the registry
    implementation("net.java.dev.jna:jna-platform:5.15.0")
    // the QR code for connecting a new device (My → My devices), the same encoder the Android app uses
    implementation("com.google.zxing:core:3.5.3")
    // KE2E (docs/security/e2e-and-phone-key.md): HPKE with Export and ChaCha20-Poly1305, lightweight API only (chat.kelo.app.core.e2e)
    implementation("org.bouncycastle:bcprov-jdk18on:1.86")

    testImplementation(kotlin("test"))
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation(compose.desktop.uiTestJUnit4)
}

tasks.test {
    useJUnit()
    // headless: the tests draw with Skia off screen (no window)
    systemProperty("java.awt.headless", "true")
}

/** Re-reads the Android strings into src/main/resources/i18n and src/generated/kotlin/chat/kelo/app/R.kt. */
tasks.register<Exec>("syncStrings") {
    group = "kelo"
    description = "Regenerates the desktop text from android/kelo/app/src/main/res/values*/strings*.xml and strings/."
    workingDir = rootProject.projectDir
    commandLine(if (System.getProperty("os.name").startsWith("Windows")) "python" else "python3", "tools/sync_strings.py")
}

/** Renders the app icons (PNG, ICO, ICNS source PNGs, MSIX tiles) from Kelo's character into packaging/icons. */
tasks.register<JavaExec>("renderIcons") {
    group = "kelo"
    description = "Draws the icons from the character (packaging/icons)."
    classpath = sourceSets.main.get().runtimeClasspath
    mainClass.set("chat.kelo.desktop.tools.RenderIconsKt")
    args(rootProject.file("packaging/icons").absolutePath)
    systemProperty("java.awt.headless", "true")
}

compose.desktop {
    application {
        mainClass = "chat.kelo.desktop.MainKt"
        jvmArgs += listOf("-Dfile.encoding=UTF-8", "-Dsun.stdout.encoding=UTF-8", "-Xmx512m")

        buildTypes.release.proguard {
            isEnabled.set(false)
        }

        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Exe)
            packageName = "Kelo"
            packageVersion = keloVersion
            description = "Kelo - your own AI agent, on your desktop"
            vendor = "Kelo"
            copyright = "© 2026 Kelo"
            // what the JVM needs: TLS with modern ciphers (jdk.crypto.ec), the tray and windows (java.desktop), preferences
            modules("java.desktop", "java.logging", "java.naming", "java.prefs", "java.net.http", "jdk.crypto.ec", "jdk.unsupported",
                "jdk.accessibility", "java.management")

            windows {
                iconFile.set(project.file("packaging/icons/kelo.ico"))
                menuGroup = "Kelo"
                shortcut = true
                menu = true
                dirChooser = true
                perUserInstall = true
                // never change: Windows uses it to tell an update of Kelo from a different program
                upgradeUuid = "6B0E6C2A-3F7D-4C55-9D7E-2A8F1B4C9E31"
            }
            macOS {
                iconFile.set(project.file("packaging/icons/kelo.icns"))
                bundleID = "chat.kelo.desktop"
                dockName = "Kelo"
            }
            linux {
                iconFile.set(project.file("packaging/icons/kelo-512.png"))
            }
        }
    }
}
