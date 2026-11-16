import Foundation
import Testing
@testable import KeloCore

/// The plan table every app must give (apps/shared/test-vectors/upload-plan.json; the Android and desktop apps read the same file).
@Suite struct UploadPlanTests {
    static func vectors() throws -> JSON {
        // …/apps/ios/KeloCore/Tests/KeloCoreTests/Upload/UploadPlanTests.swift -> the repository root is seven levels up
        var root = URL(fileURLWithPath: #filePath)
        for _ in 0..<7 { root.deleteLastPathComponent() }
        let file = root.appendingPathComponent("apps/shared/test-vectors/upload-plan.json")
        return try JSON.parse(try Data(contentsOf: file))
    }

    static func limits(_ j: JSON) -> UploadLimits {
        UploadLimits(singleMax: j["single_max"]?.int64Value, partSize: j["part_size"]?.int64Value, imageMax: j["image_max"]?.int64Value ?? 0)
    }

    @Test func everyVectorGivesItsPlan() throws {
        let v = try Self.vectors()
        let named = (v["limits"]?.objectValue ?? [:]).mapValues(Self.limits)
        let cases = try #require(v["cases"]?.arrayValue)
        #expect(cases.count >= 15)
        for c in cases {
            let name = c["name"]?.stringValue ?? "?"
            let limits = try #require(named[c["limits"]?.stringValue ?? ""], "\(name)")
            let got = UploadPlan.of(size: c["size"]?.int64Value ?? 0, isImage: c["image"]?.boolValue ?? false, limits: limits)
            let want: UploadPlan
            switch c["plan"]?.stringValue {
            case "single": want = .single
            case "needs_size": want = .needsSize
            case "too_large": want = .tooLarge(limit: c["limit"]?.int64Value ?? -1)
            case "parts": want = .parts(partSize: c["part_size"]?.int64Value ?? -1, sizes: (c["sizes"]?.arrayValue ?? []).compactMap { $0.int64Value })
            default: Issue.record("unknown plan in \(name)"); continue
            }
            #expect(got == want, "\(name)")
        }
    }

    @Test func partsAddUpToTheFile() {
        for size in [Int64(1), 99_000_001, 225_000_000, 5_000_000_000] {
            if case .parts(let part, let sizes) = UploadPlan.of(size: size, isImage: false, limits: UploadLimits(singleMax: 99_000_000, partSize: 90_000_000, imageMax: 15_000_000)) {
                #expect(sizes.reduce(0, +) == size)
                #expect(sizes.dropLast().allSatisfy { $0 == part } && (1...part).contains(sizes.last ?? 0))
            }
        }
    }

    @Test func theServersAnswerIsRead() throws {
        #expect(UploadLimits(json: try JSON.parse(#"{"single_max":99000000,"part_size":90000000,"image_max":15000000}"#)) == UploadLimits(singleMax: 99_000_000, partSize: 90_000_000, imageMax: 15_000_000))
        #expect(UploadLimits(json: try JSON.parse(#"{"single_max":99000000}"#)) == nil)
        #expect(UploadLimits(json: try JSON.parse("[1]")) == nil)
        #expect(UploadLimits(json: .string("x")) == nil)
        #expect(UploadLimits(json: try JSON.parse(#"{"single_max":null,"part_size":0,"image_max":15000000}"#)) == UploadLimits(singleMax: nil, partSize: nil, imageMax: 15_000_000))
    }

    @Test func aSizeIsWrittenInDecimalMegabytes() {
        #expect(UploadSizeText.of(15_000_000) == "15 MB")
        #expect(UploadSizeText.of(99_000_000) == "99 MB")
        #expect(UploadSizeText.of(2_000_000_000) == "2 GB")
    }
}
