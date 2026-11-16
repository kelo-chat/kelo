package chat.kelo.app.update

/**
 * The public keys (Ed25519) the apps accept for app releases: key id (first 16 hex characters of sha256 of the raw 32-byte public
 * key) -> base64 of the raw public key. Any listed key is accepted, so two can be listed at once (the current key and the next one).
 * The same map is in server/src/assistant/release_keys.py (a server test keeps them equal). Never a private key here.
 */
object ReleaseKeys {
    val ALL: Map<String, String> = mapOf(
        "307641d078a7bd67" to "5CSNXFsfquOly92OHsU6StPwJ4KazTGIt8elvNb5UUc=",   // 2026-11-15, the first release key
    )
}
