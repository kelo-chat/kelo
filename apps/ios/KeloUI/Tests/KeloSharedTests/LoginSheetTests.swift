import Testing
import Foundation
import KeloCore
@testable import KeloShared

/// The compact card and the sheet for a login or a key: what is prefilled, when Save is on, what the checkbox means, and the
/// one-time "carry on".
struct LoginSheetTests {
    @Test func theUserNameHintIsReadWhenPresentAndAbsentOtherwise() {
        let with = SecretRequest.parse(try? JSON.parse(#"{"id":"r1","kind":"login","name":"login-a","hosts":["a.com"],"username_hint":"  me@a.com "}"#))
        #expect(with?.usernameHint == "me@a.com")
        let without = SecretRequest.parse(try? JSON.parse(#"{"id":"r2","kind":"login","name":"login-b"}"#))
        #expect(without?.usernameHint == "")
        let empty = SecretRequest.parse(try? JSON.parse(#"{"id":"r3","kind":"login","name":"login-c","username_hint":""}"#))
        #expect(empty?.usernameHint == "")
    }

    @Test func aHintThatIsTooLongOrHasControlCharactersCountsAsNone() {
        #expect(UsernameHint.clean(String(repeating: "a", count: 200)).count == 200)
        #expect(UsernameHint.clean(String(repeating: "a", count: 201)) == "")
        #expect(UsernameHint.clean("a\nb") == "")
        #expect(UsernameHint.clean("a\u{0}b") == "")
        #expect(UsernameHint.clean(nil) == "")
        #expect(LoginSheetRules.initialUser(" kim@x.com ") == "kim@x.com")
    }

    @Test func saveStaysOffUntilBothFieldsAreFilled() {
        #expect(!LoginSheetRules.ready(login: true, user: "", pass: ""))
        #expect(!LoginSheetRules.ready(login: true, user: "me@a.com", pass: ""))
        #expect(!LoginSheetRules.ready(login: true, user: "  ", pass: "pw"))
        #expect(LoginSheetRules.ready(login: true, user: "me@a.com", pass: "pw"))
        // a key: one field, at least 8 characters without spaces inside
        #expect(!LoginSheetRules.ready(login: false, user: "", pass: "short"))
        #expect(LoginSheetRules.ready(login: false, user: "", pass: "sk-12345678"))
    }

    @Test func theCheckboxIsCheckedFromTheStartAndPicksTheMode() {
        #expect(LoginSheetRules.saveInKeychainByDefault)
        #expect(LoginSheetRules.mode(saveInKeychain: true) == .afterSuccess)
        #expect(LoginSheetRules.mode(saveInKeychain: false) == .once)
    }

    @Test func titlesAndTheSiteLine() {
        #expect(LoginSheetRules.title(login: true) == R.string.ios_login_add_password)
        #expect(LoginSheetRules.title(login: false) == R.string.ios_login_add_key)
        #expect(LoginSheetRules.forSite(hosts: []) == nil)
        #expect(LoginSheetRules.forSite(hosts: ["a.com"]) != nil)
        #expect(LoginSheetRules.addedKey(login: true) == R.string.ios_login_added)
    }

    @Test func theAgentIsToldToContinueOnlyOnce() {
        var once = ContinueOnce()
        let first = once.take(), second = once.take(), third = once.take()
        #expect(first)
        #expect(!second)
        #expect(!third)
    }
}
