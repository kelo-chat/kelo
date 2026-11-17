package chat.kelo.app.core

/**
 * The link from the version line in My to the source of the build, on GitHub (docs/versioning.md). It goes by RELEASE TAG, never by
 * commit: the public repository has one commit per release, tagged `android-<version>`, `ios-<version>`, `windows-<package version>`
 * or `macos-<package version>`, so the tag is the page that exists there (the private development commit never is). Whether tracked
 * files under the app's inputs differed from the release ([dirty]) decides the "modified" mark and removes the link. The iPhone app has
 * the same rule (KeloCore SourceLink.swift), with the same test vectors.
 */
object SourceLink {
    const val REPO = "https://github.com/kelo-chat/kelo"

    const val SERVER_REPO = "https://github.com/kelo-chat/kelo-server"

    /** A version is digits separated by single dots, nothing else ("1.0.2"; not "", "dev", "1.0.2-rc1", "v1", "1.0.2\n"). */
    private val VERSION = Regex("[0-9]+(\\.[0-9]+)*")

    /** `<platform>-<version>` ("android", "ios", "windows", "macos"); null when the version is not digits and dots. */
    fun appTag(platform: String, version: String): String? = if (VERSION.matches(version)) "$platform-$version" else null

    /** The release page of an app tag; null for a modified build or a missing tag. */
    fun url(tag: String?, dirty: Boolean): String? = if (tag == null || dirty) null else "$REPO/tree/$tag"

    /** What the version line shows: "android-0.10.2", or "android-0.10.2 (modified)" when dirty; [modified] is the app's word for it (my_source_modified). */
    fun label(tag: String?, dirty: Boolean, modified: String): String? = tag?.let { if (dirty) "$it ($modified)" else it }

    /** The server's tag, "release-0.10.2"; null when the server's version is not digits and dots. */
    fun serverTag(version: String): String? = if (VERSION.matches(version)) "release-$version" else null

    /** The server's release page. */
    fun serverUrl(version: String): String? = serverTag(version)?.let { "$SERVER_REPO/tree/$it" }
}
