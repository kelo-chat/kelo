import Foundation
import Testing
@testable import KeloCore

/// The address forms, shipping profiles, the in-chat form event and the order card, read from the server's real output
/// (the contract's sample files, trimmed where they repeat).
enum AddressSamples {
    static let usEn = """
    {"country":"US","country_name":"United States","lang":"en","fields":[
     {"key":"name","label":"Full name","required":true,"autocomplete":"name"},
     {"key":"address_line1","label":"Address line 1","required":true,"autocomplete":"address-line1"},
     {"key":"address_line2","label":"Address line 2 (apt, suite, unit)","required":false,"autocomplete":"address-line2"},
     {"key":"locality","label":"City","required":true,"autocomplete":"address-level2"},
     {"key":"admin_area","label":"State","required":true,"autocomplete":"address-level1",
      "options":[{"key":"AL","name":"Alabama"},{"key":"CA","name":"California"},{"key":"NY","name":"New York"}]},
     {"key":"postal_code","label":"ZIP code","required":true,"autocomplete":"postal-code","pattern":"^(?:(\\\\d{5})(?:[ \\\\-](\\\\d{4}))?)$",
      "example":"95014","numeric":true}],
     "phone":{"label":"Phone number","calling_code":"+1","required":true},"note":{"label":"Delivery note","required":false},
     "labels":{"country":"Country","nickname":"Nickname","optional":"optional"},"format":"%N%n%O%n%A%n%C, %S %Z"}
    """
    static let krKo = """
    {"country":"KR","country_name":"South Korea","lang":"ko","fields":[
     {"key":"name","label":"이름","required":true,"autocomplete":"name"},
     {"key":"postal_code","label":"우편번호","required":true,"autocomplete":"postal-code","pattern":"^(?:\\\\d{5})$","example":"03051","numeric":true},
     {"key":"address_line1","label":"주소","required":true,"autocomplete":"address-line1"},
     {"key":"address_line2","label":"상세주소","required":false,"autocomplete":"address-line2"}],
     "phone":{"label":"전화번호","calling_code":"+82","required":true},"note":{"label":"배송 메모","required":false},
     "labels":{"country":"국가","nickname":"별칭","optional":"선택"},"format":"%S %C%D%n%A%n%O%n%N%n%Z"}
    """
    static let gbEn = """
    {"country":"GB","fields":[
     {"key":"name","label":"Full name","required":true},
     {"key":"locality","label":"Town/City","required":true},
     {"key":"admin_area","label":"County","required":false},
     {"key":"postal_code","label":"Postcode","required":true,"pattern":"^(?:GIR ?0AA|(?:(?:AB|AL|B|BA|EC|SW|W)(?:\\\\d[\\\\dA-Z]? ?\\\\d[ABD-HJLN-UW-Z]{2}))|BFPO ?\\\\d{1,4})$","example":"EC1Y 8SY"}],
     "phone":{"label":"Phone number","calling_code":"+44","required":true},"labels":{"optional":"optional"}}
    """
    static let profiles = """
    {"profiles":[
     {"country":"KR","name":"홍길동","organization":"","address_line1":"서울특별시 강남구 테헤란로 152","address_line2":"10층 1004호",
      "dependent_locality":"","locality":"","admin_area":"","postal_code":"06236","sorting_code":"","phone":"+821012345678",
      "id":"ship_1a2b3c4d5e6f","label":"집","note":"문 앞에 놓아 주세요","default":true,"created_at":1762650000.0,"last_used":null,
      "formatted":"서울특별시 강남구 테헤란로 152\\n10층 1004호\\n홍길동\\n06236","one_line":"서울특별시 강남구 테헤란로 152, 10층 1004호, 06236"},
     {"country":"us","name":"Jane Roe","address_line1":"1600 Amphitheatre Pkwy","address_line2":"Bldg 43","locality":"Mountain View",
      "admin_area":"CA","postal_code":"94043","phone":"+16502530000","id":"ship_9f8e7d6c5b4a","label":"Office","default":false,
      "created_at":1762650000.0,"last_used":1762650100,"one_line":"1600 Amphitheatre Pkwy, Bldg 43, MOUNTAIN VIEW, CA 94043"},
     {"label":"no id, left out"}]}
    """
    static let shippingEvent = """
    {"type":"shipping_form","id":"call_5","form_id":"sf_7c1e2a","site":"shop.example.com","what":"블루 머그 2개","country":"kr",
     "prefill":{"name":"홍길동","phone":"010-1234-5678","postal_code":"","address_line1":"서울특별시 강남구 테헤란로 152","address_line2":""},
     "label":"집","save":true,"ask_payment":true,"expires_at":1762650600}
    """
    static let orderApproval = """
    {"type":"approval_request","approval_id":"ap_123","id":"call_9","name":"confirm_purchase",
     "arguments":{"summary":"블루 머그 2개","amount":24000,"currency":"KRW","site":"shop.example.com","card":"생활비 카드"},
     "detail":"shop.example.com에서 결제: 블루 머그 2개","lines":[],"label":"결제 확인 요청","ask":"결제할까요?","site":"shop.example.com",
     "can_remember":false,"can_remember_schedule":false,"schedule_id":null,"task_id":42,"expires_at":1762650300,
     "payment":{"site":"shop.example.com","summary":"블루 머그 2개","amount":24000,"currency":"KRW","amount_text":"24,000원",
      "items":[{"name":"블루 머그","options":"색상: 네이비 · 용량: 350ml","quantity":2,"price_text":"24,000원"},{"name":"  ","quantity":1}],
      "shipping":{"source":"profile","label":"집","name":"홍길동","phone":"+821012345678","formatted":"서울특별시 강남구 테헤란로 152\\n10층 1004호\\n06236","country":"KR"},
      "coupon":"WELCOME10","order":{"what":"블루 머그 2개"}},
     "card":{"id":"card_1","label":"생활비 카드","brand":"visa","last4":"4242","auto_pay":false,"needs_card":true,"reason":"not_auto",
      "reason_text":"이 카드는 자동 결제가 꺼져 있어요"}}
    """

    static func form(_ s: String) throws -> AddressForm { try JSON.decode(AddressForm.self, from: try JSON.parse(s)) }
}

@Suite struct AddressFormParseTests {
    @Test func readsTheUsFormInOrder() throws {
        let f = try AddressSamples.form(AddressSamples.usEn)
        #expect(f.country == "US" && f.countryName == "United States" && f.lang == "en")
        #expect(f.fields.map(\.key) == ["name", "address_line1", "address_line2", "locality", "admin_area", "postal_code"])
        #expect(f.fields[4].options.count == 3 && f.fields[4].options[1] == AddressOption(key: "CA", name: "California"))
        #expect(f.fields[5].numeric && f.fields[5].example == "95014")
        #expect(f.fields[0].pattern == nil && f.fields[0].options.isEmpty)
        #expect(f.phone == AddressPhone(label: "Phone number", callingCode: "+1", required: true))
        #expect(f.note.label == "Delivery note" && !f.note.required)
        #expect(f.labels == AddressLabels(country: "Country", nickname: "Nickname", optional: "optional"))
        #expect(f.format == "%N%n%O%n%A%n%C, %S %Z")
    }

    @Test func readsTheKoreanFormAndAMissingPhoneBlock() throws {
        let f = try AddressSamples.form(AddressSamples.krKo)
        #expect(f.fields.map(\.label) == ["이름", "우편번호", "주소", "상세주소"])
        #expect(f.phone.callingCode == "+82")
        // a form without "note" / "labels" still reads (defaults)
        let gb = try AddressSamples.form(AddressSamples.gbEn)
        #expect(gb.note == AddressNote() && gb.labels.optional == "optional" && gb.labels.country.isEmpty)
    }

    @Test func readsCountries() throws {
        let c = try JSON.decode(AddressCountries.self, from: try JSON.parse("""
            {"countries":[{"code":"AC","name":"Ascension Island"},{"code":"kr","name":"South Korea"},{"name":"no code"}],"default":"kr"}
        """))
        #expect(c.countries.map(\.code) == ["AC", "KR"])
        #expect(c.defaultCountry == "KR")
    }

    @Test func readsProfilesLeavingOutOnesWithoutId() throws {
        let list = try JSON.decode(ShippingProfiles.self, from: try JSON.parse(AddressSamples.profiles)).profiles
        #expect(list.count == 2)
        #expect(list[0].label == "집" && list[0].isDefault && list[0].phone == "+821012345678" && list[0].lastUsed == nil)
        #expect(list[0].formatted.contains("\n홍길동\n"))
        #expect(list[1].country == "US" && !list[1].isDefault && list[1].lastUsed == 1762650100 && list[1].organization.isEmpty)
        #expect(list[1].value("admin_area") == "CA" && list[1].value("nope") == "")
        // kept on the phone and read back the same
        let again = try JSON.decode(ShippingProfile.self, from: try JSON.encode(list[1]))
        #expect(again == list[1])
    }

    @Test func walletCarriesShipping() throws {
        let w = try JSON.decode(WalletInfo.self, from: try JSON.parse("""
            {"paused":false,"cards":[],"shipping":[{"id":"ship_1","label":"집","country":"KR","default":true}]}
        """))
        #expect(w.shipping.map(\.id) == ["ship_1"])
        let old = try JSON.decode(WalletInfo.self, from: try JSON.parse("{\"cards\":[]}"))
        #expect(old.shipping.isEmpty)
    }

    @Test func readsAFormAnswer() throws {
        let a = try JSON.decode(ShippingFormAnswer.self, from: try JSON.parse("""
            {"ok":true,"profile":null,"formatted":"1600 Amphitheatre Pkwy\\nMOUNTAIN VIEW, CA 94043"}
        """))
        #expect(a.ok && a.profile == nil && a.formatted.hasPrefix("1600"))
    }
}

@Suite struct AddressRulesTests {
    @Test func optionalLabels() throws {
        let f = try AddressSamples.form(AddressSamples.usEn)
        #expect(AddressRules.label(f.fields[0], optional: f.labels.optional) == "Full name")
        #expect(AddressRules.label(f.fields[2], optional: f.labels.optional) == "Address line 2 (apt, suite, unit) (optional)")
        #expect(AddressRules.label("상세주소", required: false, optional: "선택") == "상세주소 (선택)")
        #expect(AddressRules.label("x", required: false, optional: " ") == "x")
    }

    @Test func postalPatterns() throws {
        let us = try AddressSamples.form(AddressSamples.usEn).fields[5]
        #expect(AddressRules.postalOK(us, "95014"))
        #expect(AddressRules.postalOK(us, "95014-1234"))
        #expect(AddressRules.postalOK(us, " 95014 1234 "))
        #expect(!AddressRules.postalOK(us, "9501"))
        #expect(!AddressRules.postalOK(us, "950141"))
        #expect(!AddressRules.postalOK(us, "95014-12"))
        #expect(AddressRules.postalOK(us, ""))   // empty is the required check's business
        let kr = try AddressSamples.form(AddressSamples.krKo).fields[1]
        #expect(AddressRules.postalOK(kr, "06236") && !AddressRules.postalOK(kr, "135-080"))
        let gb = try AddressSamples.form(AddressSamples.gbEn).fields[3]
        #expect(AddressRules.postalOK(gb, "EC1Y 8SY"))
        #expect(AddressRules.postalOK(gb, "ec1y 8sy"))   // case-insensitive, as on the server
        #expect(!AddressRules.postalOK(gb, "12345"))
        #expect(AddressRules.postalOK(AddressField(key: "postal_code"), "anything"))
        #expect(AddressRules.postalOK(AddressField(key: "postal_code", pattern: "(unclosed"), "x"))   // a pattern ICU cannot read: the server decides
    }

    @Test func problemsOfAnUsAddress() throws {
        let f = try AddressSamples.form(AddressSamples.usEn)
        var d = AddressDraft(country: "US")
        var p = AddressRules.problems(f, d)
        #expect(p["name"] == .required && p["address_line1"] == .required && p["locality"] == .required)
        #expect(p["admin_area"] == .required && p["postal_code"] == .required && p["phone"] == .required)
        #expect(p["address_line2"] == nil)
        d.values = ["name": "Jane Roe", "address_line1": "1600 Amphitheatre Pkwy", "locality": "Mountain View", "admin_area": "california",
                    "postal_code": "9404"]
        d.phone = "(650) 253-0000"
        p = AddressRules.problems(f, d)
        #expect(p == ["postal_code": .postal(example: "95014")])
        d["postal_code"] = "94043"
        d["admin_area"] = "Atlantis"
        #expect(AddressRules.problems(f, d) == ["admin_area": .required])
        d["admin_area"] = "CA"
        d.phone = "650 253 0000 ext. 5"
        #expect(AddressRules.problems(f, d) == ["phone": .phone])
        d.phone = "+1 650-253-0000"
        #expect(AddressRules.problems(f, d).isEmpty)
        d.phone = "1+650"
        #expect(AddressRules.problems(f, d) == ["phone": .phone])
    }

    @Test func phoneShownWithoutTheCallingCode() {
        #expect(AddressRules.phoneShown("+821012345678", callingCode: "+82") == "1012345678")
        #expect(AddressRules.phoneShown("+16502530000", callingCode: "+1") == "6502530000")
        #expect(AddressRules.phoneShown("+16502530000", callingCode: "+82") == "+16502530000")   // another country's number: as is
        #expect(AddressRules.phoneShown("010-1234-5678", callingCode: "+82") == "010-1234-5678")   // as typed
        #expect(AddressRules.phoneShown("+82", callingCode: "+82") == "+82")
        #expect(AddressRules.phoneShown("+821012345678", callingCode: "") == "+821012345678")
    }

    @Test func changingTheCountryKeepsSharedKeys() throws {
        let us = try AddressSamples.form(AddressSamples.usEn)
        let kr = try AddressSamples.form(AddressSamples.krKo)
        let fromUs = ["name": "Jane", "address_line1": "1 Main St", "locality": "Austin", "admin_area": "TX", "postal_code": "73301"]
        #expect(AddressRules.carry(fromUs, to: kr) == ["name": "Jane", "address_line1": "1 Main St", "postal_code": "73301"])
        // a picked value stays only when the new list has it (TX is not one of the trimmed sample's states)
        #expect(AddressRules.carry(fromUs, to: us)["admin_area"] == nil)
        #expect(AddressRules.carry(["admin_area": "New York"], to: us) == ["admin_area": "NY"])
        #expect(AddressRules.normalized(["admin_area": "california", "name": "x"], for: us) == ["admin_area": "CA", "name": "x"])
        #expect(AddressRules.optionName(us.fields[4], "ca") == "California")
        #expect(AddressRules.optionName(us.fields[4], "") == nil)
    }

    @Test func bodyForANewAndAChangedAddress() throws {
        let kr = try AddressSamples.form(AddressSamples.krKo)
        var d = AddressDraft(country: "KR", values: ["name": " 홍길동 ", "postal_code": "06236", "address_line1": "테헤란로 152", "locality": "stale"],
                             phone: " 010-1234-5678 ", note: " 문 앞 ")
        let b = AddressRules.body(kr, d)
        #expect(b["country"] == .string("KR") && b["name"] == .string("홍길동") && b["phone"] == .string("010-1234-5678"))
        #expect(b["note"] == .string("문 앞") && b["address_line2"] == .string(""))
        // keys the Korean form does not have go empty for a new address (no stale city from another country)
        #expect(b["locality"] == .string("") && b["admin_area"] == .string("") && b["organization"] == .string(""))
        // same country when editing: keys the form does not show are left as stored
        let same = AddressRules.body(kr, d, savedCountry: "kr")
        #expect(same["locality"] == nil && same["organization"] == nil)
        let moved = AddressRules.body(kr, d, savedCountry: "US")
        #expect(moved["locality"] == .string("") && moved["admin_area"] == .string(""))
        // a picked value goes as its key
        let us = try AddressSamples.form(AddressSamples.usEn)
        d = AddressDraft(country: "US", values: ["admin_area": "New York"])
        #expect(AddressRules.body(us, d)["admin_area"] == .string("NY"))
    }

    @Test func draftsFromAProfileAndAPrefill() throws {
        let p = try JSON.decode(ShippingProfiles.self, from: try JSON.parse(AddressSamples.profiles)).profiles[1]
        let d = AddressDraft.of(p)
        #expect(d.country == "US" && d["locality"] == "Mountain View" && d["organization"] == "" && d.values["organization"] == nil)
        #expect(d.phone == "+16502530000")
        let f = AddressDraft.prefilled(country: "kr", ["name": "홍길동", "phone": "010", "note": "문 앞", "postal_code": "", "color": "red"])
        #expect(f.country == "KR" && f.values == ["name": "홍길동"] && f.phone == "010" && f.note == "문 앞")
    }

    @Test func countriesInTheAppLanguage() {
        let list = [AddressCountry(code: "US", name: "United States"), AddressCountry(code: "KR", name: "South Korea"),
                    AddressCountry(code: "AC", name: "Ascension Island"), AddressCountry(code: "KR", name: "dup")]
        let ko = AddressRules.shown(list, locale: Locale(identifier: "ko"))
        #expect(ko.count == 3)
        #expect(ko.first { $0.code == "KR" }?.name == "대한민국")
        #expect(ko.first { $0.code == "US" }?.name == "미국")
        // Hangul order: 대한민국 before 미국
        #expect(ko.firstIndex { $0.code == "KR" }! < ko.firstIndex { $0.code == "US" }!)
        let en = AddressRules.shown(list, locale: Locale(identifier: "en"))
        #expect(en.map(\.code) == ["AC", "KR", "US"])
        #expect(AddressRules.countryName("AA", english: "Nowhere", locale: Locale(identifier: "en")) == "Nowhere")
        #expect(AddressRules.countryName("AA", locale: Locale(identifier: "en")) == "AA")
        #expect(AddressRules.countryName("AC", english: "x", locale: Locale(identifier: "en")) == "Ascension Island")
    }

    @Test func smallTexts() {
        #expect(AddressRules.oneLine("서울특별시 강남구 테헤란로 152\n\n 10층 \n06236") == "서울특별시 강남구 테헤란로 152, 10층, 06236")
        #expect(AddressRules.startLabel("  ", fallback: "집") == "집")
        #expect(AddressRules.startLabel(String(repeating: "a", count: 50), fallback: "집").count == AddressRules.maxLabel)
        #expect(ShippingFormRules.doneLine(label: "집", oneLine: "테헤란로 152") == "집 · 테헤란로 152")
        #expect(ShippingFormRules.doneLine(label: nil, oneLine: "테헤란로 152") == "테헤란로 152")
        #expect(ShippingFormRules.open(expiresAt: 100, now: 99) && !ShippingFormRules.open(expiresAt: 100, now: 100))
        #expect(ShippingFormRules.open(expiresAt: nil, now: 1e12))
    }

    @Test func formAnswerBody() {
        let body = ShippingFormRules.body(address: ["country": "KR"], save: true, label: " 집 ", payment: "card")
        #expect(body["save"] == .bool(true) && body["label"] == .string("집") && body["payment"] == .string("card"))
        #expect(body["address"]?["country"] == .string("KR"))
        let once = ShippingFormRules.body(address: [:], save: false, label: "집", payment: nil)
        #expect(once["label"] == nil && once["payment"] == .null && once["save"] == .bool(false))
    }
}

@Suite struct ShippingEventTests {
    @Test func readsTheShippingFormEvent() throws {
        guard case .shippingForm(let f) = TurnEvent.parse(try JSON.parse(AddressSamples.shippingEvent)) else {
            Issue.record("not a shipping form"); return
        }
        #expect(f.id == "call_5" && f.formId == "sf_7c1e2a" && f.site == "shop.example.com" && f.what == "블루 머그 2개")
        #expect(f.country == "KR" && f.label == "집" && f.save && f.askPayment && f.expiresAt == 1762650600)
        // blank prefill values are left out
        #expect(f.prefill == ["name": "홍길동", "phone": "010-1234-5678", "address_line1": "서울특별시 강남구 테헤란로 152"])
    }

    @Test func aShippingFormWithoutFormIdIsIgnoredAndSaveDefaultsOn() throws {
        #expect(TurnEvent.parse(try JSON.parse("{\"type\":\"shipping_form\",\"id\":\"x\"}")) == .unknown("shipping_form"))
        guard case .shippingForm(let f) = TurnEvent.parse(try JSON.parse("{\"type\":\"shipping_form\",\"form_id\":\"sf\",\"prefill\":null}")) else {
            Issue.record("not read"); return
        }
        #expect(f.save && !f.askPayment && f.prefill.isEmpty && f.expiresAt == nil)
    }

    @Test func readsTheOrderApproval() throws {
        guard case .approvalRequest(let a) = TurnEvent.parse(try JSON.parse(AddressSamples.orderApproval)), let p = a.payment else {
            Issue.record("no payment"); return
        }
        #expect(p.amountText == "24,000원" && p.currency == "KRW")
        #expect(p.items == [OrderItem(name: "블루 머그", options: "색상: 네이비 · 용량: 350ml", quantity: 2, priceText: "24,000원")])
        #expect(p.shipping?.source == OrderShipping.fromProfile && p.shipping?.label == "집" && p.shipping?.country == "KR")
        #expect(p.shipping?.lines == ["서울특별시 강남구 테헤란로 152", "10층 1004호", "06236"])
        #expect(p.coupon == "WELCOME10" && p.orderWhat == "블루 머그 2개")
        guard let card = a.card else { Issue.record("no card"); return }
        #expect(OrderRules.payWith(card.card) == "생활비 카드 ••4242")
        #expect(OrderRules.shipHead(p.shipping!) == "집 · 홍길동 · +821012345678")
        #expect(OrderRules.quantity(p.items[0]) == "× 2")
    }

    @Test func anOlderPaymentHasNoOrderParts() throws {
        guard case .approvalRequest(let a) = TurnEvent.parse(try JSON.parse("""
            {"type":"approval_request","approval_id":"p","name":"confirm_purchase","payment":{"site":"s","summary":"책","amount":1,
             "shipping":{"source":"site","formatted":"  "},"coupon":" ","items":"no"}}
        """)), let p = a.payment else { Issue.record("no payment"); return }
        #expect(p.items.isEmpty && p.shipping == nil && p.coupon.isEmpty && p.orderWhat == nil)
    }

    @Test func siteAddressAndSmallParts() throws {
        let s = OrderShipping(source: "site", formatted: "Jane Roe\n1 Main St")
        #expect(OrderRules.shipHead(s).isEmpty && s.lines.count == 2)
        #expect(OrderRules.payWith(CardBrief(id: "c", label: "", brand: "visa", last4: "4242")) == "Visa ••4242")
        #expect(OrderRules.payWith(CardBrief(id: "c", label: "Work", brand: "visa", last4: "")) == "Work")
        #expect(OrderRules.quantity(OrderItem(name: "x")) == nil)
    }

    @Test func autoPaymentCarriesItemsAndShipping() throws {
        guard case .paymentAuto(let p) = TurnEvent.parse(try JSON.parse("""
            {"type":"payment_auto","id":"x","payment":{"site":"s","amount_text":"1원","items":[{"name":"a","quantity":3}],
             "shipping":{"source":"once","name":"Jane","formatted":"1 Main St"}}}
        """)) else { Issue.record("not read"); return }
        #expect(p.items.first?.quantity == 3 && p.shipping?.source == "once")
    }
}
