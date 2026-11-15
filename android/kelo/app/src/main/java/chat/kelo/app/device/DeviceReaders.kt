package chat.kelo.app.device

import android.content.ContentResolver
import android.net.Uri
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony

/** Reads this phone's contacts, texts and call log (read only; each only after its permission was granted). */
object DeviceReaders {

    fun contacts(cr: ContentResolver): List<ContactRow> {
        val cols = arrayOf(
            ContactsContract.Data.CONTACT_ID, ContactsContract.Data.MIMETYPE, ContactsContract.Data.DISPLAY_NAME,
            ContactsContract.Data.DATA1, ContactsContract.Data.DATA2, ContactsContract.Data.DATA3, ContactsContract.Data.DATA4,
        )
        val wanted = listOf(
            ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE, ContactsContract.CommonDataKinds.Email.CONTENT_ITEM_TYPE,
            ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE, ContactsContract.CommonDataKinds.Event.CONTENT_ITEM_TYPE,
            ContactsContract.CommonDataKinds.StructuredName.CONTENT_ITEM_TYPE,
        )
        class Acc(var name: String = "", val phones: MutableList<Pair<String, String>> = mutableListOf(), val emails: MutableList<String> = mutableListOf(),
                  var org: String = "", var title: String = "", var birthday: String = "")
        val byId = LinkedHashMap<Long, Acc>()
        val sel = "${ContactsContract.Data.MIMETYPE} IN (${wanted.joinToString(",") { "?" }})"
        cr.query(ContactsContract.Data.CONTENT_URI, cols, sel, wanted.toTypedArray(), "${ContactsContract.Data.DISPLAY_NAME} ASC")?.use { c ->
            while (c.moveToNext() && byId.size <= DeviceRules.MAX_CONTACTS) {
                val a = byId.getOrPut(c.getLong(0)) { Acc() }
                if (a.name.isBlank()) a.name = c.getString(2).orEmpty()
                val d1 = c.getString(3).orEmpty()
                when (c.getString(1)) {
                    ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE -> if (d1.isNotBlank()) {
                        val type = if (c.isNull(4)) 0 else c.getInt(4)
                        a.phones += d1 to phoneLabel(type, c.getString(5).orEmpty())
                    }
                    ContactsContract.CommonDataKinds.Email.CONTENT_ITEM_TYPE -> if (d1.contains('@')) a.emails += d1
                    ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE -> {
                        if (a.org.isBlank()) a.org = d1
                        if (a.title.isBlank()) a.title = c.getString(6).orEmpty()   // DATA4 = TITLE
                    }
                    ContactsContract.CommonDataKinds.Event.CONTENT_ITEM_TYPE ->
                        if (!c.isNull(4) && c.getInt(4) == ContactsContract.CommonDataKinds.Event.TYPE_BIRTHDAY) a.birthday = d1
                }
            }
        }
        return byId.values.map { ContactRow(it.name, it.phones, it.emails, it.org, it.title, it.birthday) }
    }

    private fun phoneLabel(type: Int, custom: String): String = when (type) {
        ContactsContract.CommonDataKinds.Phone.TYPE_MOBILE -> "mobile"
        ContactsContract.CommonDataKinds.Phone.TYPE_HOME -> "home"
        ContactsContract.CommonDataKinds.Phone.TYPE_WORK, ContactsContract.CommonDataKinds.Phone.TYPE_WORK_MOBILE -> "work"
        ContactsContract.CommonDataKinds.Phone.TYPE_MAIN -> "main"
        ContactsContract.CommonDataKinds.Phone.TYPE_CUSTOM -> custom.take(30)
        else -> ""
    }

    /** Received and sent SMS, and MMS text, since [sinceMs]. */
    fun messages(cr: ContentResolver, sinceMs: Long): List<MessageRow> {
        val out = mutableListOf<MessageRow>()
        val cols = arrayOf(Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.TYPE, Telephony.Sms.THREAD_ID)
        val sel = "${Telephony.Sms.DATE} >= ? AND ${Telephony.Sms.TYPE} IN (${Telephony.Sms.MESSAGE_TYPE_INBOX}, ${Telephony.Sms.MESSAGE_TYPE_SENT})"
        runCatching {
            cr.query(Telephony.Sms.CONTENT_URI, cols, sel, arrayOf(sinceMs.toString()), "${Telephony.Sms.DATE} DESC")?.use { c ->
                while (c.moveToNext() && out.size < DeviceRules.MAX_MESSAGES) {
                    val addr = c.getString(0).orEmpty()
                    if (addr.isBlank()) continue
                    out += MessageRow(addr, c.getString(1).orEmpty(), c.getLong(2), c.getInt(3) == Telephony.Sms.MESSAGE_TYPE_SENT, thread = c.getLong(4))
                }
            }
        }
        runCatching { out += mms(cr, sinceMs, 500) }
        return out
    }

    private fun mms(cr: ContentResolver, sinceMs: Long, max: Int): List<MessageRow> {
        val out = mutableListOf<MessageRow>()
        val cols = arrayOf(Telephony.Mms._ID, Telephony.Mms.DATE, Telephony.Mms.MESSAGE_BOX, Telephony.Mms.THREAD_ID)
        val sel = "${Telephony.Mms.DATE} >= ? AND ${Telephony.Mms.MESSAGE_BOX} IN (${Telephony.Mms.MESSAGE_BOX_INBOX}, ${Telephony.Mms.MESSAGE_BOX_SENT})"
        cr.query(Telephony.Mms.CONTENT_URI, cols, sel, arrayOf((sinceMs / 1000).toString()), "${Telephony.Mms.DATE} DESC")?.use { c ->
            while (c.moveToNext() && out.size < max) {
                val id = c.getLong(0)
                val sent = c.getInt(2) == Telephony.Mms.MESSAGE_BOX_SENT
                val text = mmsText(cr, id)
                val addr = mmsAddress(cr, id, sent)
                if (addr.isBlank()) continue
                out += MessageRow(addr, text, c.getLong(1) * 1000, sent, mms = true, thread = c.getLong(3))
            }
        }
        return out
    }

    private fun mmsText(cr: ContentResolver, id: Long): String {
        val sb = StringBuilder()
        cr.query(Uri.parse("content://mms/part"), arrayOf("_id", "ct", "text"), "mid = ?", arrayOf(id.toString()), null)?.use { c ->
            while (c.moveToNext()) if (c.getString(1) == "text/plain") sb.append(c.getString(2).orEmpty())
        }
        return sb.toString()
    }

    private fun mmsAddress(cr: ContentResolver, id: Long, sent: Boolean): String {
        val want = if (sent) 151 else 137   // PduHeaders.TO / FROM
        cr.query(Uri.parse("content://mms/$id/addr"), arrayOf("address", "type"), null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val a = c.getString(0).orEmpty()
                if (c.getInt(1) == want && a.isNotBlank() && a != "insert-address-token") return a
            }
        }
        return ""
    }

    fun calls(cr: ContentResolver, sinceMs: Long): List<CallRow> {
        val out = mutableListOf<CallRow>()
        val cols = arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION)
        cr.query(CallLog.Calls.CONTENT_URI, cols, "${CallLog.Calls.DATE} >= ?", arrayOf(sinceMs.toString()), "${CallLog.Calls.DATE} DESC")?.use { c ->
            while (c.moveToNext() && out.size < DeviceRules.MAX_CALLS) {
                out += CallRow(c.getString(0).orEmpty(), c.getString(1).orEmpty(), c.getInt(2), c.getLong(3), c.getLong(4))
            }
        }
        return out
    }
}
