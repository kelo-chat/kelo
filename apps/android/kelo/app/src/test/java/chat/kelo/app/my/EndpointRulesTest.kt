package chat.kelo.app.my

import chat.kelo.app.my.EndpointRules.Problem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EndpointRulesTest {
    @Test fun usableAddresses() {
        assertNull(EndpointRules.check("http://192.168.0.10:3001/v1"))
        assertNull(EndpointRules.check(" https://llm.example.com/v1 "))
        assertNull(EndpointRules.check("HTTP://localhost:1234/v1"))
        assertNull(EndpointRules.check("http://[::1]:8080/v1"))
    }

    @Test fun whatIsWrong() {
        assertEquals(Problem.EMPTY, EndpointRules.check("  "))
        assertEquals(Problem.SPACES, EndpointRules.check("http://my host/v1"))
        assertEquals(Problem.SCHEME, EndpointRules.check("192.168.0.10:3001/v1"))
        assertEquals(Problem.SCHEME, EndpointRules.check("ftp://x/v1"))
        assertEquals(Problem.SCHEME, EndpointRules.check("javascript:alert(1)"))
        assertEquals(Problem.HOST, EndpointRules.check("http:///v1"))
        assertEquals(Problem.HOST, EndpointRules.check("https://"))
    }
}
