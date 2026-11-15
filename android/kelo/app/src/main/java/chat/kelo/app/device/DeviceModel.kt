package chat.kelo.app.device

/**
 * The phone's model as people know it ("Galaxy S24 Ultra"), from Build.MANUFACTURER / Build.MODEL and the vendor's marketing-name
 * property. Never the user's own device name (Settings "device_name" can hold a person's name). Pure, so it can be tested.
 */
object DeviceModel {
    /** Samsung model codes (the part before the region letter) -> marketing names (Galaxy S and Z, 2021 on); any other code falls back to "Galaxy (code)". */
    private val SAMSUNG = mapOf(
        "SM-G991" to "Galaxy S21", "SM-G996" to "Galaxy S21+", "SM-G998" to "Galaxy S21 Ultra", "SM-G990" to "Galaxy S21 FE",
        "SM-S901" to "Galaxy S22", "SM-S906" to "Galaxy S22+", "SM-S908" to "Galaxy S22 Ultra",
        "SM-S911" to "Galaxy S23", "SM-S916" to "Galaxy S23+", "SM-S918" to "Galaxy S23 Ultra", "SM-S711" to "Galaxy S23 FE",
        "SM-S921" to "Galaxy S24", "SM-S926" to "Galaxy S24+", "SM-S928" to "Galaxy S24 Ultra", "SM-S721" to "Galaxy S24 FE",
        "SM-S931" to "Galaxy S25", "SM-S936" to "Galaxy S25+", "SM-S938" to "Galaxy S25 Ultra", "SM-S937" to "Galaxy S25 Edge",
        "SM-F926" to "Galaxy Z Fold3", "SM-F936" to "Galaxy Z Fold4", "SM-F946" to "Galaxy Z Fold5", "SM-F956" to "Galaxy Z Fold6",
        "SM-F966" to "Galaxy Z Fold7",
        "SM-F711" to "Galaxy Z Flip3", "SM-F721" to "Galaxy Z Flip4", "SM-F731" to "Galaxy Z Flip5", "SM-F741" to "Galaxy Z Flip6",
        "SM-F766" to "Galaxy Z Flip7",
    )

    /**
     * [manufacturer]/[model]: Build.MANUFACTURER / Build.MODEL; [marketName]: the vendor's "ro.product.marketname" (Xiaomi and others
     * set it; "" when absent).
     */
    fun name(manufacturer: String, model: String, marketName: String = ""): String {
        val m = model.trim()
        marketName.trim().takeIf { it.isNotEmpty() && it.length <= 30 }?.let { return it }
        if (m.startsWith("SM-")) {
            SAMSUNG[m.take(7)]?.let { return it }
            return "Galaxy ($m)".take(30)
        }
        if (m.isEmpty()) return "Android"
        val maker = manufacturer.trim()
        // "Pixel 8", "moto g84": the model already says what it is; else put the maker in front ("OnePlus CPH2581" stays readable)
        return when {
            maker.isEmpty() || m.contains(maker, ignoreCase = true) || m.startsWith("Pixel") -> m
            else -> "${maker.replaceFirstChar { it.uppercase() }} $m"
        }.take(30)
    }

    /** The vendor's marketing-name property, read without a permission ("" when the phone has none). */
    fun marketNameProperty(): String = runCatching {
        val c = Class.forName("android.os.SystemProperties")
        c.getMethod("get", String::class.java, String::class.java).invoke(null, "ro.product.marketname", "") as String
    }.getOrDefault("")
}
