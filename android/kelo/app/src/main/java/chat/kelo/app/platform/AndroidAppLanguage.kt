package chat.kelo.app.platform

import chat.kelo.app.AppContainer
import chat.kelo.app.LocaleSupport
import chat.kelo.app.ui.ActivityHost

/** The app's language pick behind [AppLanguage] (the whole app is shown again in the chosen language). */
class AndroidAppLanguage(private val container: AppContainer, private val host: ActivityHost) : AppLanguage {
    override val systemShownCode: String get() = LocaleSupport.systemShownCode()
    override val current: String? get() = container.store.language
    override fun pick(code: String) = LocaleSupport.pick(host.current, container, code)
}
