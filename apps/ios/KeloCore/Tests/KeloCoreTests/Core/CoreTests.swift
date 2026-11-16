import Foundation
import Testing
@testable import KeloCore

@Suite struct PairingLinksTests {
    let code = "AbCdEfGh_ijkl-MNOP1234"

    @Test func parsesTheServersLink() {
        #expect(PairingLinks.parse("https://kelo.example.com/#pair=\(code)") == PairingLink(serverUrl: "https://kelo.example.com", code: code))
    }

    @Test func keepsPortAndLowercasesHost() {
        #expect(PairingLinks.parse("https://Me.Tail1234.TS.net:8443/#pair=\(code)") == PairingLink(serverUrl: "https://me.tail1234.ts.net:8443", code: code))
    }

    @Test func acceptsNoSlashBeforeHashAndSurroundingSpace() {
        #expect(PairingLinks.parse("  https://kelo.example.com#pair=\(code) \n") == PairingLink(serverUrl: "https://kelo.example.com", code: code))
    }

    @Test func decodesPercentEscapesButNotPlus() {
        #expect(PairingLinks.parse("https://h.example/#pair=abc+def%2Fghi%3Djklmnop")?.code == "abc+def/ghi=jklmnop")
    }

    @Test func httpForLocalDevelopment() throws {
        let link = try #require(PairingLinks.parse("http://10.0.2.2:8000/#pair=\(code)"))
        #expect(link.serverUrl == "http://10.0.2.2:8000")
        #expect(ServerAddress.isAllowed(link.serverUrl, debug: true))
    }

    @Test func rejectsWhatIsNotAPairingLink() {
        for bad in ["", "https://kelo.example.com/", "#pair=\(code)", "kelo.example.com/#pair=\(code)", "ftp://kelo.example.com/#pair=\(code)",
                    "https://user:pw@kelo.example.com/#pair=\(code)", "https://kelo.example.com/?x=1#pair=\(code)",
                    "https://kelo.example.com/#pair=short", "https://kelo.example.com/#pair=" + String(repeating: "x", count: 129),
                    "https://kelo.example.com/#pair=abcdefgh ijklmnopq", "https://kelo.example.com/#pair=%ZZbadescapes1234567"] {
            #expect(PairingLinks.parse(bad) == nil, "\(bad)")
        }
    }

    @Test func normalizesTypedServerAddresses() {
        #expect(ServerAddress.normalize("kelo.example.com/") == "https://kelo.example.com")
        #expect(ServerAddress.normalize(" HTTPS://Kelo.Example.com ") == "https://kelo.example.com")
        #expect(ServerAddress.normalize("https://h.example/kelo/") == "https://h.example/kelo")
        #expect(ServerAddress.normalize("http://localhost:8000") == "http://localhost:8000")
        #expect(ServerAddress.normalize("") == nil)
        #expect(ServerAddress.normalize("kelo example") == nil)
        #expect(ServerAddress.normalize("mailto:me@example.com") == nil)
        #expect(ServerAddress.normalize("kelo.example.com", defaultScheme: nil) == nil)
    }

    @Test func httpsOnlyExceptLocalHosts() {
        #expect(ServerAddress.isAllowed("https://kelo.example.com", debug: true))
        #expect(ServerAddress.isAllowed("http://127.0.0.1:8000", debug: true))
        #expect(ServerAddress.isAllowed("http://localhost", debug: true))
        #expect(!ServerAddress.isAllowed("http://kelo.example.com", debug: true))
        #expect(!ServerAddress.isAllowed("http://100.101.102.103:8000", debug: true))
        // a release build talks https only, even to a local host
        #expect(!ServerAddress.isAllowed("http://10.0.2.2:8000", debug: false))
        #expect(ServerAddress.isAllowed("https://kelo.example.com", debug: false))
    }
}

@Suite struct AcceptLanguageTests {
    @Test func appLanguageComesFirstWithThePhonesRegion() {
        #expect(AcceptLanguage.build(appLanguage: "ko", deviceTags: ["en-US", "ko-KR"]) == "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7")
    }

    @Test func aPhoneWithoutTheAppLanguageStillAsksForItFirst() {
        #expect(AcceptLanguage.build(appLanguage: "ko", deviceTags: ["en-US"]) == "ko,en-US;q=0.9,en;q=0.8")
    }

    @Test func noAppPickUsesThePhonesLanguagesInOrder() {
        #expect(AcceptLanguage.build(appLanguage: nil, deviceTags: ["ja-JP", "ko-KR"]) == "ja-JP,ja;q=0.9,ko-KR;q=0.8,ko;q=0.7")
    }

    @Test func tagsAreCleanedRepeatsAndUnknownsDroppedListCapped() {
        #expect(AcceptLanguage.build(appLanguage: "ko", deviceTags: ["ko_kr", "und", "", "KO-KR"]) == "ko-KR,ko;q=0.9")
        #expect(AcceptLanguage.build(appLanguage: nil, deviceTags: ["de-DE", "fr-FR", "es-ES", "it-IT"]).split(separator: ",").count == 6)
        #expect(AcceptLanguage.build(appLanguage: nil, deviceTags: ["zh-Hant-TW"]) == "zh-TW,zh;q=0.9")
        #expect(AcceptLanguage.build(appLanguage: nil, deviceTags: ["es-419"]) == "es-419,es;q=0.9")
        #expect(AcceptLanguage.build(appLanguage: nil, deviceTags: []) == "")
    }

    @Test func everyCallCarriesTheHeader() async throws {
        let t = FakeTransport(.ok(200, "[]"), .ok(200, "[]"))
        try await KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "ko-KR,ko;q=0.9" }).request("GET", "/api/connectors")
        #expect(t.last.value(forHTTPHeaderField: "Accept-Language") == "ko-KR,ko;q=0.9")
        try await KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" }).request("GET", "/api/connectors")
        #expect(t.last.value(forHTTPHeaderField: "Accept-Language") == nil)
    }
}

@Suite struct KeloApiTests {
    @Test func sendsBearerAndDecodes() async throws {
        let t = FakeTransport(.ok(200, #"{"devices":[{"id":1,"name":"폰","extra":true}]}"#))
        let list = try await KeloApi(baseUrl: "https://k.example/", token: "tok", transport: t).devices()
        #expect(list.devices.map(\.name) == ["폰"])
        #expect(list.current == nil)   // an older server says nothing
        #expect(t.last.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
        #expect(t.last.url?.absoluteString == "https://k.example/api/devices")
    }

    @Test func postsJson() async throws {
        let t = FakeTransport(.ok(200, #"{"id":2,"name":"t"}"#))
        let g: Device = try await KeloApi(baseUrl: "https://k.example", token: "tok", transport: t).post("/api/goals", body: ["title": "t"])
        #expect(g.id == 2)
        #expect(t.last.httpMethod == "POST")
        #expect(t.last.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("application/json") == true)
        #expect(t.lastBody == #"{"title":"t"}"#)
    }

    @Test func pairSendsNoAuthorization() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (200, ["content-type": "application/json"], [Data(#"{"token":"dev-token","device_id":7}"#.utf8)]) }
        let link = try #require(PairingLinks.parse("https://kelo.test/#pair=0123456789abcdefXYZ&k=\(s.key.base64url)"))
        let session = try await PairingClient(transport: s).pair(link, deviceName: "iPhone · Kelo")
        #expect(session == Session(serverUrl: "https://kelo.test", token: "dev-token", e2e: KE2EPin(key: s.key, verified: true)))
        let inner = try #require(s.inner.first)
        #expect(inner.head.headers["authorization"] == nil && inner.head.method == "POST" && inner.head.path == "/api/pair")
        #expect(String(decoding: inner.body, as: UTF8.self) == #"{"code":"0123456789abcdefXYZ","name":"iPhone · Kelo"}"#)
    }

    @Test func checkKeyReadsTheProfile() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (200, ["content-type": "application/json"], [Data(#"{"user_name":"민지","agent_name":"켈로","avatar_version":3}"#.utf8)]) }
        let (session, profile) = try await PairingClient(transport: s).checkKey(serverUrl: "https://kelo.test", key: "key")
        #expect(session.token == "key" && session.e2e?.verified == false)
        #expect(profile.agentName == "켈로" && profile.avatarVersion == 3)
        #expect(s.inner.first?.head.headers["authorization"] == "Bearer key")
    }

    @Test func errorCarriesDetailAnd401SignsOut() async {
        let t = FakeTransport(.ok(401, #"{"detail":"invalid or missing bearer token"}"#))
        let signedOut = Box(false)
        let api = KeloApi(baseUrl: "https://k.example", token: "bad", transport: t, onUnauthorized: { signedOut.value = true })
        await #expect(throws: ApiError(status: 401, detail: "invalid or missing bearer token")) { try await api.profile() }
        #expect(signedOut.value)
    }

    @Test func nonStringDetailFallsBackToStatus() async {
        let t = FakeTransport(.ok(422, #"{"detail":[{"loc":["body"]}]}"#))
        await #expect(throws: ApiError(status: 422, detail: "HTTP 422")) { try await KeloApi(baseUrl: "https://k", token: "t", transport: t).profile() }
    }

    @Test func aWebPageOrWrongShapeIsAnErrorNotACrash() async {
        let t = FakeTransport(.ok(200, "<html>captive portal</html>"), .ok(200, #"{"items":"nope"}"#))
        let api = KeloApi(baseUrl: "https://k", token: "t", transport: t)
        do { _ = try await api.profile(); Issue.record("no error") } catch let e as ApiError { #expect(e.isNetwork) } catch { Issue.record("\(error)") }
        // a list where an object was expected
        do { _ = try await api.connectors(); Issue.record("no error") } catch let e as ApiError { #expect(e.isNetwork) } catch { Issue.record("\(error)") }
    }

    @Test func unreachableIsANetworkError() async {
        let t = FakeTransport(.fail(URLError(.cannotConnectToHost)))
        do { _ = try await KeloApi(baseUrl: "https://k", token: "t", transport: t).profile(); Issue.record("no error") }
        catch let e as ApiError { #expect(e.isNetwork && e.isTransient) } catch { Issue.record("\(error)") }
    }

    @Test func uploadsRawBytes() async throws {
        let t = FakeTransport(.ok(200, #"{"path":"uploads/a b.pdf"}"#))
        let path = try await KeloApi(baseUrl: "https://k", token: "t", transport: t).upload(name: "a b.pdf", body: .data(Data([1, 2, 3])), contentType: "application/pdf")
        #expect(path == "uploads/a b.pdf")
        #expect(t.last.httpMethod == "PUT")
        #expect(t.last.url?.absoluteString == "https://k/api/uploads/a%20b.pdf")
        #expect(t.last.httpBody?.count == 3)
    }

    @Test func emptyAnswerIsNull() async throws {
        let t = FakeTransport(.ok(204, ""))
        #expect(try await KeloApi(baseUrl: "https://k", token: "t", transport: t).request("DELETE", "/api/goals/3") == .null)
        #expect(t.last.httpMethod == "DELETE")
    }

    @Test func segmentsAreEncodedLikeEncodeURIComponent() {
        #expect(KeloApi.segment("a b/c?d&e") == "a%20b%2Fc%3Fd%26e")
        #expect(KeloApi.segment("task:abc") == "task%3Aabc")
        #expect(KeloApi.segment("한") == "%ED%95%9C")
        #expect(KeloApi.segment("A-z_0.~*'()!") == "A-z_0.~*'()!")
    }

    @Test func transientRule() {
        #expect(ApiError(status: 0, detail: "").isTransient)
        #expect(ApiError(status: 503, detail: "").isTransient)
        #expect(!ApiError(status: 404, detail: "").isTransient)
    }
}

@Suite struct JSONAndModelTests {
    @Test func lenientReading() throws {
        let p = try JSON.decode(Profile.self, from: try JSON.parse(#"{"user_name":null,"agent_name":5,"avatar_version":"12","new_field":[1]}"#))
        #expect(p.userName == "" && p.agentName == "5" && p.avatarVersion == 12)
    }

    @Test func listsLeaveOutEntriesWithoutTheirId() throws {
        let page = try JSON.decode(TaskPage.self, from: try JSON.parse(#"{"items":[{"id":1,"status":"running"},{"title":"no id"},{"id":"3"}],"unseen":2}"#))
        #expect(page.items.map(\.id) == [1, 3])
        #expect(page.items[0].running && page.items[0].seen)
        #expect(page.unseen == 2 && page.nextBefore == nil)
    }

    @Test func agentStatusDefaultsAndRunning() throws {
        let s = try JSON.decode(AgentStatus.self, from: try JSON.parse(
            #"{"busy":true,"status":"Opening a site","running":[{"task_id":3,"title":"t","waiting_approval":true}],"pending_approvals":1}"#))
        #expect(s.busy && s.running.first?.waitingApproval == true && s.avatarVersion == -1 && s.pendingApprovals == 1)
    }

    @Test func walletCardNeverHasANumberField() {
        let names = Mirror(reflecting: WalletCard(id: "c")).children.compactMap(\.label)
        #expect(!names.contains("number") && !names.contains("cvc"))
        #expect(names.contains("last4"))
    }

    @Test func compactJsonIsStable() {
        let j: JSON = ["b": 1, "a": [true, nil, "x/y"], "c": 1.5]
        #expect(j.compactString == #"{"a":[true,null,"x/y"],"b":1,"c":1.5}"#)
    }
}

/// Android PairingLinksTest.aSignInCodeTypedInTheKeyFieldIsRecognized: a code typed in the access-key field is tried with POST /api/pair.
@Suite struct TypedPairingCodeTests {
    @Test func aSignInCodeTypedInTheKeyFieldIsRecognized() {
        #expect(PairingLinks.looksLikeCode("ABCD-EFGH-JKMN-PQRS"))
        #expect(PairingLinks.looksLikeCode("  abcdefghjkmnpqrs "))
        #expect(!PairingLinks.looksLikeCode("short"))
        #expect(PairingLinks.looksLikeCode("ABCD EFGH JKMN PQRS"))
        #expect(!PairingLinks.looksLikeCode("ABCD\tEFGH\nJKMN PQRS"))
        #expect(!PairingLinks.looksLikeCode(String(repeating: "a", count: 129)))
    }

    @Test func spacesBetweenGroupsAreTakenOutBeforePairing() {
        #expect(PairingLinks.codeOf(" ABCD EFGH JKMN PQRS ") == "ABCDEFGHJKMNPQRS")
        #expect(PairingLinks.codeOf("ABCD-EFGH-JKMN-PQRS") == "ABCD-EFGH-JKMN-PQRS")
    }
}
