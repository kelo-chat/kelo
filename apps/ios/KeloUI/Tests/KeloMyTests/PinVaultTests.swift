import Testing
import Foundation
import CryptoKit
import KeloCore
import KeloShared
@testable import KeloMy

/// Android wallet/PinVaultTest.kt: a payment PIN sealed with the phone's key, opened again, sent as bytes; the form's checks; the release.
@Suite struct PinVaultTests {
    @Test func onlyFourToEightDigitsGetIn() {
        #expect(PinSecret.of("1234") != nil && PinSecret.of("12345678") != nil)
        #expect(PinSecret.of("123") == nil && PinSecret.of("123456789") == nil && PinSecret.of("12a4") == nil)
        #expect(!String(describing: PinSecret.of("4321")!).contains("4321"))
    }

    @Test func sealedWithThePhonesKeyOpensOnlyWithIt() throws {
        let key = SymmetricKey(size: .bits256)
        let blob = try PinBlob.seal(PinSecret.of("482913")!, key: key, keyId: "k1")
        #expect(blob.alg == PinBlob.alg && blob.keyId == "k1" && !blob.blob.contains("482913"))
        let opened = PinBlob.open(blob, key: key)
        #expect(opened?.length == 6)
        #expect(PinBlob.open(blob, key: SymmetricKey(size: .bits256)) == nil)
        var other = blob; other.alg = CardBlob.alg
        #expect(PinBlob.open(other, key: key) == nil)
    }

    @Test func theBodyCarriesThePinAsBytes() throws {
        let body = PinJson.body(["pin_id": .string("p1")], PinSecret.of("1234")!)
        let parsed = try JSON.parse(body)
        #expect(parsed["pin_id"] == .string("p1") && parsed["pin"] == .string("1234"))
        #expect(try JSON.parse(PinJson.body([:], PinSecret.of("9876")!))["pin"] == .string("9876"))
    }

    @Test func theFormChecksPinTwiceAndASite() {
        #expect(PinRules.clean("12-34 56789") == "12345678")
        #expect(PinRules.ready(pin: "1234", again: "1234", site: "shop.example.com"))
        #expect(!PinRules.ready(pin: "1234", again: "1235", site: "shop.example.com"))
        #expect(!PinRules.ready(pin: "1234", again: "1234", site: "shop"))
        #expect(PinRules.siteOK("https://pay.example.co.kr/checkout"))
        #expect(PinRules.mismatch(pin: "1234", again: "1235") && !PinRules.mismatch(pin: "1234", again: "12"))
    }

    @Test func aPinFromAnotherPhoneIsNotReleasedHere() async {
        let pin = PaymentPin(id: "p1", site: "shop", digits: 4, e2eAlg: PinBlob.alg, e2eKeyId: "other")
        let err = await PinRelease.run(pin: pin, keyId: "mine", blob: { E2eBlob(blob: "", alg: "", keyId: "") },
                                       unlock: { .cancelled }, send: { _ in })
        #expect(err == .notOnPhone)
        let key = SymmetricKey(size: .bits256)
        let mine = PaymentPin(id: "p1", site: "shop", digits: 4, e2eAlg: PinBlob.alg, e2eKeyId: "k")
        let blob = try! PinBlob.seal(PinSecret.of("2580")!, key: key, keyId: "k")
        var sent = 0
        let ok = await PinRelease.run(pin: mine, keyId: "k", blob: { blob }, unlock: { .key(key) }, send: { s in sent = s.length })
        #expect(ok == nil && sent == 4)
        let cancelled = await PinRelease.run(pin: mine, keyId: "k", blob: { blob }, unlock: { .cancelled }, send: { _ in })
        #expect(cancelled == .cancelled)
    }
}

/// Android device/PhonePlaceTest + my/BriefingSettings rules.
@Suite struct BriefingSettingsTests {
    @MainActor @Test func thePhonesPlaceGoesWhenAllowedAndNotTooOften() {
        let h = PhonePlace.everySeconds
        #expect(PhonePlace.due(granted: true, lastAt: 0, now: h))
        #expect(!PhonePlace.due(granted: true, lastAt: 100, now: 100 + h - 1))
        #expect(PhonePlace.due(granted: true, lastAt: 500, now: 100))   // the clock moved back
        #expect(!PhonePlace.due(granted: false, lastAt: 0, now: h))
        // what it sends: the position as it is (precise or approximate), its accuracy when known, the town when known
        let b = PhonePlace.body(lat: 37.498123, lon: 127.027612, accuracy: 35, name: "강남구", country: "KR")
        #expect(b["lat"] == .number(37.498123) && b["accuracy"] == .number(35) && b["name"] == .string("강남구"))
        let bare = PhonePlace.body(lat: 37.5, lon: 127, accuracy: nil, name: nil, country: "")
        #expect(bare["accuracy"] == nil && bare["name"] == nil && bare["country_code"] == nil)
    }

    @Test func theLocationLineShowsOnlyWhileTheWeatherHasNoPlace() throws {
        let none = BriefingSettings()                                  // weather on, no phone sent a location yet
        var known = none
        known.weather.here = BriefingPlaceInfo(name: "서울", lat: 37.5, lon: 127, countryCode: "KR")
        #expect(BriefingSettingsRules.needsLocation(none, granted: false))
        #expect(BriefingSettingsRules.needsLocation(none, granted: true))      // allowed, nothing sent yet
        #expect(BriefingSettingsRules.needsLocation(known, granted: false))    // this phone is not allowed
        #expect(!BriefingSettingsRules.needsLocation(known, granted: true))
        var off = none
        off.sections = ["weather": false]
        #expect(!BriefingSettingsRules.needsLocation(off, granted: false))     // no weather wanted
        let read = try JSON.decode(BriefingSettings.self, from: try JSON.parse(
            #"{"weather":{"provider":"auto","here":{"name":"부산","lat":35.1,"lon":129.0,"country_code":"KR","at":1.0,"accuracy_m":30}}}"#))
        #expect(read.weather.here?.name == "부산")
    }

    @Test func settingsRules() throws {
        #expect(BriefingSettingsRules.offsets(current: 15) == [0, 10, 15, 20, 30, 45, 60])
        #expect(BriefingSettingsRules.offsets(current: 99).last == 60)
        #expect(BriefingSettingsRules.keyOK("abcd1234") && !BriefingSettingsRules.keyOK("abc 1234") && !BriefingSettingsRules.keyOK("short"))
        let b = try JSON.decode(BriefingSettings.self, from: try JSON.parse(#"{"sections":{"weather":false}}"#))
        #expect(b.enabled && b.timeMode == "auto" && b.fixedTime == "07:30" && b.earlyOpen)
        #expect(!BriefingSettingsRules.sectionOn(b, "weather") && BriefingSettingsRules.sectionOn(b, "missed"))
        let s = try JSON.decode(BriefingSources.self, from: try JSON.parse("""
            {"calendar":{"mode":"only","accounts":[{"id":"c1","kind":"calendar","name":"iCloud","address":"a@icloud.com","on":true}]},"mail":{"accounts":[]}}
        """))
        #expect(BriefingSourcesRules.shown(s) == ["calendar"])
        #expect(BriefingSourcesRules.title(s.calendar.accounts[0]) == "a@icloud.com" && BriefingSourcesRules.subtitle(s.calendar.accounts[0]) == "iCloud")
        #expect(BriefingSourcesRules.flipped(s, "calendar", "c1", false).calendar.accounts[0].on == false)
        #expect(BriefingSourcesRules.toggleBody("mail", "m1", true) == ["mail": ["accounts": ["m1": true]]])
    }

    @Test func anOutlookMailboxAndANestAccountReadCleanly() throws {
        let s = try JSON.decode(BriefingSources.self, from: try JSON.parse("""
            {"mail":{"accounts":[
              {"id":"mail:outlook","kind":"mail","provider":"outlook","name":"Outlook Mail","address":"me@outlook.com","label":"Outlook","on":true,"problem":null},
              {"id":"mail:nest-acme","kind":"mail","provider":"nest:acme","name":"Acme Mail Package","address":"","label":"Acme","on":true,"problem":null}]},
             "calendar":{"accounts":[{"id":"nest:acme","kind":"calendar","provider":"nest:acme","name":"Acme Calendar","address":"","on":true}]}}
        """))
        let outlook = s.mail.accounts[0], nest = s.mail.accounts[1]
        #expect(BriefingSourcesRules.title(outlook) == "me@outlook.com" && BriefingSourcesRules.subtitle(outlook) == "Outlook Mail")
        #expect(BriefingSourcesRules.title(nest) == "Acme Mail Package" && BriefingSourcesRules.subtitle(nest) == "")   // the name only
        #expect(BriefingSourcesRules.title(s.calendar.accounts[0]) == "Acme Calendar")
        #expect(nest.problem == nil)
    }
}
