package chat.kelo.app.device

import org.junit.Assert.assertEquals
import org.junit.Test

class DeviceModelTest {
    @Test fun `Samsung codes become marketing names`() {
        assertEquals("Galaxy S24", DeviceModel.name("samsung", "SM-S921N"))
        assertEquals("Galaxy S24 Ultra", DeviceModel.name("samsung", "SM-S928B"))
        assertEquals("Galaxy Z Flip6", DeviceModel.name("samsung", "SM-F741N"))
    }

    @Test fun `an unknown Samsung code stays readable`() = assertEquals("Galaxy (SM-A556E)", DeviceModel.name("samsung", "SM-A556E"))
    @Test fun `a Pixel keeps its model`() = assertEquals("Pixel 8", DeviceModel.name("Google", "Pixel 8"))
    @Test fun `the maker goes in front of a bare code`() = assertEquals("OnePlus CPH2581", DeviceModel.name("OnePlus", "CPH2581"))
    @Test fun `the vendor's marketing name wins`() = assertEquals("Xiaomi 14", DeviceModel.name("Xiaomi", "23127PN0CG", "Xiaomi 14"))
    @Test fun `nothing known`() = assertEquals("Android", DeviceModel.name("", ""))
}
