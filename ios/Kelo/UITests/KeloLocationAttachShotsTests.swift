import XCTest

/// Backlog 24/26 screens (KELO_LOCATION_SHOTS=1) against `desktop/tools/mock_server.py` with KELO_MOCK_LOCATION_CARD=1 (and
/// KELO_MOCK_LOCATION_SETTINGS=1 for the "refused before" card): the chat history with the user's own files (one photo sent with
/// nothing typed, three photos with long text, two documents with long names), the waiting location card the pending list brings
/// back at the end, and a photo opened full screen. Other variables as KeloScreensTests: KELO_SERVER, KELO_KEY, KELO_LANG, KELO_TAG,
/// KELO_SHOTS (screens are named `<tag>-<name>.png`). Without KELO_LOCATION_SHOTS the test is skipped.
final class KeloLocationAttachShotsTests: KeloShotCase {


    func testLocationCardAndFiles() throws {
        guard env["KELO_LOCATION_SHOTS"] == "1", env["KELO_SERVER"] != nil, let key = env["KELO_KEY"] else {
            throw XCTSkip("KELO_LOCATION_SHOTS, KELO_SERVER and KELO_KEY are needed")
        }
        launchApp()
        signIn(key)

        // the waiting card comes back from the pending list at the end of the chat
        let card = element("kelo.locationAsk")
        XCTAssertTrue(card.waitForExistence(timeout: 20), "no location card\n\(app.debugDescription)")
        pause(2)   // thumbnails load
        shot("location-card")

        // the files further up: the bubble with only a photo, three photos with text, two document chips
        let chip = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "notes.docx")).firstMatch
        var n = 0
        while !(chip.exists && chip.isHittable) && n < 6 { app.swipeDown(velocity: .slow); n += 1 }
        pause(1)
        shot("files-documents")
        let photo = element("IMG_2041.jpg")
        n = 0
        while !(photo.exists && photo.isHittable) && n < 8 { app.swipeDown(velocity: .slow); n += 1 }
        pause(1.5)
        shot("files-photos")
        if photo.exists && photo.isHittable {
            photo.tap()
            pause(1.5)
            shot("photo-full")
        }
    }
}
