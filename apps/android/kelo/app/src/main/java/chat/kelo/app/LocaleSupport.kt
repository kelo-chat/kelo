package chat.kelo.app

import android.app.Activity
import android.app.LocaleManager
import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import android.os.Build
import android.os.LocaleList
import chat.kelo.app.core.Languages
import chat.kelo.app.core.SecureStore
import java.util.Locale
import kotlinx.coroutines.launch

/**
 * The app's language: the phone's own language by default ([Languages.SYSTEM]; one the app lacks shows English, the default
 * resources), or the one picked in My → Language. There is no language step on first launch; a pick made on the first screen of
 * an earlier version is kept as it was.
 *
 * On Android 13+ the pick is also the system's per-app language (Settings → Apps → Kelo → Language), kept in step both ways; on
 * older phones the activity's context is wrapped in the picked locale.
 */
object LocaleSupport {
    fun wrap(base: Context, code: String?): Context {
        val tag = Languages.override(code) ?: return base
        val config = Configuration(base.resources.configuration)
        config.setLocales(LocaleList(Locale.forLanguageTag(tag)))
        return base.createConfigurationContext(config)
    }

    /** Remembers [code] and tells the system (Android 13+), so its per-app language setting shows the same. */
    fun choose(context: Context, store: SecureStore, code: String) {
        store.language = code
        store.languageSynced = true
        setSystemLocales(context, Languages.override(code))
    }

    /**
     * At start, Android 13+: a language set in the system's per-app setting wins (it was changed there); a pick made before this
     * phone's app knew about that setting is handed to the system once. Nothing picked anywhere (a new install): the phone's
     * language is followed and nothing is written.
     */
    fun syncWithSystem(context: Context, store: SecureStore) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val lm = context.getSystemService(LocaleManager::class.java) ?: return
        val system = runCatching { lm.applicationLocales }.getOrNull() ?: return
        val step = LanguageSync.step(store.language, store.languageSynced, if (system.isEmpty) null else system[0].toLanguageTag())
        store.languageSynced = true
        when (step) {
            LanguageSync.Step.Nothing -> Unit
            is LanguageSync.Step.HandToSystem -> setSystemLocales(context, Languages.override(step.code))
            is LanguageSync.Step.Keep -> store.language = step.code
        }
    }

    private fun setSystemLocales(context: Context, tag: String?) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val lm = context.getSystemService(LocaleManager::class.java) ?: return
        runCatching { lm.applicationLocales = if (tag == null) LocaleList.getEmptyLocaleList() else LocaleList.forLanguageTags(tag) }
    }

    /**
     * A language picked on screen: kept, handed to the system, requests ask for it, and the screen is made again when its
     * language changes.
     */
    fun pick(activity: Activity?, container: AppContainer, code: String) {
        val differs = differsFromShown(activity, code)
        choose(container.appContext, container.store, code)
        container.refreshAcceptLanguage()
        tellServer(container, code)
        if (differs) activity?.recreate()
    }

    /**
     * The server keeps the owner's language for what it says on its own (notifications, the briefing, scheduled runs) and changes
     * it only from a pick (PUT /api/language), never from a request's language. Only on a pick, not at every start, so one phone
     * in another language does not flip it. Signed in only; errors (an older server's 404 too) are ignored.
     */
    private fun tellServer(container: AppContainer, code: String) {
        val api = container.sessions.api.value ?: return
        val language = Languages.forServer(code, phoneLanguageTags(container.appContext))
        val body = kotlinx.serialization.json.buildJsonObject { put("language", kotlinx.serialization.json.JsonPrimitive(language)) }
        (container.appContext as? KeloApp)?.appScope?.launch { runCatching { api.request("PUT", "/api/language", body) } }
    }

    /** True when the screen on show is not in the language [code] resolves to (so it should be made again). */
    fun differsFromShown(activity: Activity?, code: String): Boolean {
        val shown = activity?.resources?.configuration?.locales?.get(0)?.toLanguageTag() ?: return true
        val want = Languages.override(code) ?: deviceLanguageTags().firstOrNull() ?: return true
        return Languages.match(shown) != Languages.match(want) || Languages.match(shown) == null
    }

    /** The phone's own languages (not the app's), most preferred first, e.g. ["en-US", "ko-KR"]. */
    fun deviceLanguageTags(): List<String> {
        val list = Resources.getSystem().configuration.locales
        return (0 until list.size()).map { list[it].toLanguageTag() }
    }

    /**
     * The phone's own languages read from the system on Android 13+ ([LocaleManager.getSystemLocales]): right after a pick the
     * process's [Resources.getSystem] can still carry the app's previous per-app language.
     */
    private fun phoneLanguageTags(context: Context): List<String> {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val list = runCatching { context.getSystemService(LocaleManager::class.java)?.systemLocales }.getOrNull()
            if (list != null && !list.isEmpty) return (0 until list.size()).map { list[it].toLanguageTag() }
        }
        return deviceLanguageTags()
    }

    /** The language the app is shown in when following the phone ([Languages.SYSTEM]), as one of [Languages.ALL]. */
    fun systemShownCode(): String = Languages.suggested(deviceLanguageTags())
}

/** What [LocaleSupport.syncWithSystem] does at start (Android 13+), apart from the Android calls so it can be tested. */
object LanguageSync {
    sealed interface Step {
        /** Nothing to do: nothing picked anywhere (the phone's language is followed), or already in step. */
        data object Nothing : Step
        /** A pick made in the app before it knew the system setting: hand it to the system once. */
        data class HandToSystem(val code: String) : Step
        /** Keep [code] as the app's language (what the system's per-app setting says now). */
        data class Keep(val code: String) : Step
    }

    /** [stored] the app's own pick (null: none yet), [synced] it was handed to the system before, [systemTag] the system's per-app language. */
    fun step(stored: String?, synced: Boolean, systemTag: String?): Step = when {
        !synced && systemTag == null && stored != null -> Step.HandToSystem(stored)
        systemTag == null && stored == null -> Step.Nothing
        systemTag == null -> if (stored == Languages.SYSTEM) Step.Nothing else Step.Keep(Languages.SYSTEM)
        else -> Step.Keep(Languages.match(systemTag) ?: Languages.SYSTEM)
    }
}
