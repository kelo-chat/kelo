import Testing
import Foundation
@testable import KeloConnectors

@Suite struct ConnectorIconRulesTests {
    let big = "/api/connectors/icons/notion.png?v=0123456789abcdef"
    let small = "/api/connectors/icons/notion-128.png?v=fedcba9876543210"

    @Test func smallTilesUseTheSmallIcon() {
        #expect(ConnectorIconRules.pick(icon: big, small: small, size: 40) == small)
        #expect(ConnectorIconRules.pick(icon: big, small: small, size: 64) == big)
        #expect(ConnectorIconRules.pick(icon: big, small: "", size: 40) == big)
        #expect(ConnectorIconRules.pick(icon: "", small: small, size: 64) == small)
        #expect(ConnectorIconRules.pick(icon: "", small: "", size: 40) == "")
    }

    @Test func onlyTheServersOwnIconPathsAreFetched() {
        #expect(ConnectorIconRules.valid(big) && ConnectorIconRules.valid(small))
        for bad in ["https://evil.example/api/connectors/icons/notion.png", "/api/connectors/icons/../me.png", "/api/uploads/x.png",
                    "/api/connectors/icons/Notion.png", "/api/connectors/icons/notion.svg", ""] {
            #expect(!ConnectorIconRules.valid(bad), "\(bad)")
        }
    }

    @Test func keptFilesAreNamedByIconAndVersion() {
        #expect(ConnectorIconRules.fileName(big) == "notion-0123456789abcdef.png")
        #expect(ConnectorIconRules.fileName(small) == "notion-128-fedcba9876543210.png")
        #expect(ConnectorIconRules.stale(big, kept: "notion-00000000aaaaaaaa.png"))
        #expect(!ConnectorIconRules.stale(big, kept: "notion-0123456789abcdef.png"))
        #expect(!ConnectorIconRules.stale(big, kept: "notion-128-fedcba9876543210.png"))
        #expect(ConnectorIconRules.stale(small, kept: "notion-128-00000000aaaaaaaa.png"))
    }

    @Test func colourIsReadFromHex() {
        let c = ConnectorIconRules.rgb("#FF8000")
        #expect(c?.0 == 1 && c?.2 == 0)
        #expect(ConnectorIconRules.rgb("#12345") == nil && ConnectorIconRules.rgb("zzzzzz") == nil)
    }

    @Test func iconFieldsDecodeAndAreOptional() throws {
        let x = try JSONDecoder().decode(ConnectorInfo.self, from: Data(##"{"id":"notion","icon":"/api/connectors/icons/notion.png?v=ab","icon_small":"/s","icon_color":"#000000"}"##.utf8))
        #expect(x.icon.hasSuffix("?v=ab") && x.iconSmall == "/s" && x.iconColor == "#000000")
        let y = try JSONDecoder().decode(ConnectorInfo.self, from: Data(#"{"id":"x"}"#.utf8))
        #expect(y.icon.isEmpty && y.iconSmall.isEmpty && y.iconColor.isEmpty)
    }
}
