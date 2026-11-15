plugins {
    alias(libs.plugins.android.test)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.baselineprofile)
}

android {
    namespace = "chat.kelo.baselineprofile"
    compileSdk = 36
    buildToolsVersion = "36.0.0"

    defaultConfig {
        minSdk = 28
        targetSdk = 36
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    targetProjectPath = ":app"

    // the app has three builds (sideload, play, full); the profile is made with the full one and used by both (src/release)
    flavorDimensions += "store"
    productFlavors {
        create("sideload") { dimension = "store" }
        create("play") { dimension = "store" }
        create("full") { dimension = "store" }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

// runs on the phone or emulator connected with adb (it must be signed in to a Kelo server for the full journey)
baselineProfile {
    useConnectedDevices = true
}

dependencies {
    implementation(libs.androidx.junit)
    implementation(libs.androidx.uiautomator)
    implementation(libs.androidx.benchmark.macro)
}

// The plugin hooks the on-device run into this module's `assemble` (and so into a root `./gradlew assemble` or `build`). That run
// reinstalls and finally uninstalls the app on the connected phone, signing it out. Run it only when it was asked for by name
// (generate...BaselineProfile, connected...AndroidTest).
val deviceRunRequested = gradle.startParameter.taskNames.map { it.substringAfterLast(':') }.any {
    it.contains("BaselineProfile") || it.startsWith("connected")
}
tasks.matching { it.name.startsWith("connected") || it.name.startsWith("collect") }.configureEach {
    onlyIf { deviceRunRequested }
}
