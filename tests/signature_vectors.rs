//! Independently written harness for separately licensed Wycheproof data.
use lit::crypto::signatures::PQSignature;

#[test]
fn signature_verification_matches_external_vectors() {
    let fixture: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/cryptography/mldsa87-verification.json"))
            .unwrap();
    let mut checked = 0;
    for group in fixture["testGroups"].as_array().unwrap() {
        let public_key = hex::decode(group["publicKey"].as_str().unwrap()).unwrap();
        for case in group["tests"].as_array().unwrap() {
            let signature = PQSignature {
                signature: hex::decode(case["sig"].as_str().unwrap()).unwrap(),
                public_key: public_key.clone(),
                algorithm: "ML-DSA-87".into(),
            };
            let message = hex::decode(case["msg"].as_str().unwrap()).unwrap();
            assert_eq!(
                signature.verify(&message).is_ok(),
                case["result"].as_str().unwrap() == "valid",
                "Wycheproof case {}: {}",
                case["tcId"],
                case["comment"]
            );
            checked += 1;
        }
    }
    assert_eq!(checked, 6);
}
