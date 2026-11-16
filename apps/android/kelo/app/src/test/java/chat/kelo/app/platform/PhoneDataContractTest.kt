package chat.kelo.app.platform

import java.lang.reflect.Modifier
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone's PhoneDataSection must have a method for every abstract method of the shared interface. When the interface's composable
 * method had default values, the app's class got one more parameter and the Connectors tab crashed with an AbstractMethodError.
 */
class PhoneDataContractTest {
    @Test fun theAndroidClassImplementsEveryAbstractMethodOfTheInterface() {
        val impl = AndroidPhoneData.javaClass
        val abstract = PhoneDataSection::class.java.methods.filter { Modifier.isAbstract(it.modifiers) }
        assertTrue(abstract.isNotEmpty())
        for (m in abstract) {
            val found = runCatching { impl.getMethod(m.name, *m.parameterTypes) }.getOrNull()
            assertTrue("${m.name}(${m.parameterTypes.joinToString { it.simpleName }}) is missing in AndroidPhoneData", found != null && !Modifier.isAbstract(found.modifiers))
        }
    }
}
