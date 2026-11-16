import Testing
@testable import KeloShared

@Suite struct DeviceModelsTests {
    @Test func deviceModelNames() {
        #expect(DeviceModels.name(identifier: "iPhone16,1", kind: "iPhone") == "iPhone 15 Pro")
        #expect(DeviceModels.name(identifier: "iPhone18,4", kind: "iPhone") == "iPhone Air")
        #expect(DeviceModels.name(identifier: "iPhone99,9", kind: "iPhone") == "iPhone")
        #expect(DeviceModels.name(identifier: "iPad16,3", kind: "iPad") == "iPad")
    }
}
