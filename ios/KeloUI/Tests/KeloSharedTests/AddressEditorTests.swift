import Testing
import Foundation
import KeloCore
@testable import KeloShared

/// A server answering the address calls from fixed text; every request is kept.
private final class AddressTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [String] = []
    var paths: [String] { lock.withLock { log } }

    static let countries = #"{"countries":[{"code":"US","name":"United States"},{"code":"KR","name":"South Korea"},{"code":"JP","name":"Japan"}],"default":"KR"}"#
    static let us = #"""
    {"country":"US","fields":[{"key":"name","label":"Full name","required":true},{"key":"address_line1","label":"Address line 1","required":true},
     {"key":"locality","label":"City","required":true},{"key":"admin_area","label":"State","required":true,
      "options":[{"key":"CA","name":"California"},{"key":"NY","name":"New York"}]},
     {"key":"postal_code","label":"ZIP code","required":true,"pattern":"^(?:(\\d{5})(?:[ \\-](\\d{4}))?)$","example":"95014"}],
     "phone":{"label":"Phone number","calling_code":"+1","required":true},"note":{"label":"Delivery note"},
     "labels":{"country":"Country","nickname":"Nickname","optional":"optional"}}
    """#
    static let kr = #"""
    {"country":"KR","fields":[{"key":"name","label":"이름","required":true},{"key":"postal_code","label":"우편번호","required":true,
      "pattern":"^(?:\\d{5})$","example":"03051"},{"key":"address_line1","label":"주소","required":true},
     {"key":"address_line2","label":"상세주소","required":false}],
     "phone":{"label":"전화번호","calling_code":"+82","required":true},"labels":{"country":"국가","optional":"선택"}}
    """#

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        let path = request.url!.path
        lock.withLock { log.append(path) }
        switch path {
        case "/api/address/countries": return (200, Data(Self.countries.utf8))
        case "/api/address/form/US": return (200, Data(Self.us.utf8))
        case "/api/address/form/KR": return (200, Data(Self.kr.utf8))
        default: return (404, Data(#"{"detail":"unknown country code"}"#.utf8))
        }
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

@Suite(.serialized) struct AddressEditorTests {
    @MainActor private func api(_ t: AddressTransport) -> KeloApi {
        AddressEditor.forget()
        return KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t, acceptLanguage: { "en" })
    }

    @MainActor @Test func aNewAddressStartsAtTheServersCountry() async {
        let t = AddressTransport()
        let e = AddressEditor(draft: AddressDraft(country: ""))
        await e.start(api(t))
        #expect(e.draft.country == "KR")
        #expect(e.form?.fields.map(\.key) == ["name", "postal_code", "address_line1", "address_line2"])
        #expect(e.countries.count == 3)
        #expect(t.paths == ["/api/address/countries", "/api/address/form/KR"])
    }

    @MainActor @Test func editingShowsThePhoneWithoutTheCallingCodeAndChecksTheZip() async {
        let t = AddressTransport()
        let p = ShippingProfile(id: "ship_9", label: "Office", country: "US", name: "Jane Roe", addressLine1: "1600 Amphitheatre Pkwy",
                                locality: "Mountain View", adminArea: "california", postalCode: "9404", phone: "+16502530000")
        let e = AddressEditor(draft: .of(p), savedCountry: p.country)
        await e.start(api(t))
        #expect(e.draft.phone == "6502530000")
        #expect(e.draft["admin_area"] == "CA")   // a name the list knows becomes its key
        #expect(!e.check())
        #expect(e.shownProblems == ["postal_code": .postal(example: "95014")])
        e.draft["postal_code"] = "94043"
        #expect(e.check())
        let body = e.body()
        #expect(body?["admin_area"] == .string("CA") && body?["phone"] == .string("6502530000"))
        #expect(body?["address_line2"] == nil)   // a row the US form does not show, same country: left as stored
    }

    @MainActor @Test func changingTheCountryKeepsWhatBothFormsHave() async {
        let t = AddressTransport()
        let e = AddressEditor(draft: AddressDraft(country: "US", values: ["name": "Jane", "locality": "Austin", "admin_area": "NY",
                                                                          "postal_code": "10001"], phone: "212 555 0100"))
        let a = api(t)
        await e.start(a)
        await e.setCountry("kr", api: a)
        #expect(e.draft.country == "KR" && e.form?.country == "KR")
        #expect(e.draft.values == ["name": "Jane", "postal_code": "10001"])
        #expect(e.draft.phone == "212 555 0100")
        // back to the US: the kept form is used, no new request
        await e.setCountry("US", api: a)
        #expect(t.paths.filter { $0 == "/api/address/form/US" }.count == 1)
        #expect(e.problems["locality"] == .required)
    }

    @MainActor @Test func aFormThatCannotLoadSaysSo() async {
        let t = AddressTransport()
        let e = AddressEditor(draft: AddressDraft(country: "ZZ"))
        await e.start(api(t))
        #expect(e.form == nil && e.failed != nil)
        #expect(!e.check() && e.body() == nil)
    }
}
