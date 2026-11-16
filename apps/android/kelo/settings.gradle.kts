pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "kelo"
include(":app")
include(":baselineprofile")

// the code Android and the desktop app share (see apps/shared/build.gradle.kts)
includeBuild("../../shared")
