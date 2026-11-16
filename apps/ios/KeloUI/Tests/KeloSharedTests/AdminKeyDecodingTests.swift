import Foundation
import Testing
import KeloCore
@testable import KeloShared

@Suite struct AdminKeyDecodingTests {
    @Test func adminKeyInfoDecoding() throws {
        let json = """
        {
            "supported": true,
            "set": true,
            "hint": "sk-adm"
        }
        """
        let info = try JSONDecoder().decode(AdminKeyInfo.self, from: Data(json.utf8))
        #expect(info.supported == true)
        #expect(info.set == true)
        #expect(info.hint == "sk-adm")

        let empty = "{}"
        let def = try JSONDecoder().decode(AdminKeyInfo.self, from: Data(empty.utf8))
        #expect(def.supported == false)
        #expect(def.set == false)
        #expect(def.hint == nil)
    }

    @Test func providerInfoDecodesAdminKey() throws {
        let json = """
        {
            "name": "openai",
            "admin_key": {
                "supported": true,
                "set": true,
                "hint": "sk-test"
            }
        }
        """
        let p = try JSONDecoder().decode(ModelProvider.self, from: Data(json.utf8))
        #expect(p.name == "openai")
        #expect(p.adminKey.supported == true)
        #expect(p.adminKey.set == true)
        #expect(p.adminKey.hint == "sk-test")

        let missing = """
        {
            "name": "anthropic"
        }
        """
        let p2 = try JSONDecoder().decode(ModelProvider.self, from: Data(missing.utf8))
        #expect(p2.name == "anthropic")
        #expect(p2.adminKey.supported == false)
        #expect(p2.adminKey.set == false)
        #expect(p2.adminKey.hint == nil)
    }

    @Test func providerAccountDecodesAdminAndAdminError() throws {
        let json = """
        {
            "provider": "openai",
            "admin": true,
            "admin_error": "Key refused"
        }
        """
        let a = try JSONDecoder().decode(ProviderAccount.self, from: Data(json.utf8))
        #expect(a.provider == "openai")
        #expect(a.admin == true)
        #expect(a.adminError == "Key refused")

        let missing = """
        {
            "provider": "anthropic"
        }
        """
        let a2 = try JSONDecoder().decode(ProviderAccount.self, from: Data(missing.utf8))
        #expect(a2.provider == "anthropic")
        #expect(a2.admin == false)
        #expect(a2.adminError == nil)
    }
}
