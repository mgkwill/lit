use pqcrypto_mldsa::mldsa87;
use pqcrypto_traits::sign::{DetachedSignature as _, PublicKey as _};
fn main() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!("../vectors.json")).unwrap();
    let mut failures = 0;
    for group in fixture["testGroups"].as_array().unwrap() {
        let pk = mldsa87::PublicKey::from_bytes(&hex::decode(group["publicKey"].as_str().unwrap()).unwrap());
        for case in group["tests"].as_array().unwrap() {
            let sig = mldsa87::DetachedSignature::from_bytes(&hex::decode(case["sig"].as_str().unwrap()).unwrap());
            let msg = hex::decode(case["msg"].as_str().unwrap()).unwrap();
            let ctx = hex::decode(case.get("ctx").and_then(|v|v.as_str()).unwrap_or("")).unwrap();
            let accepted = match (&sig, &pk) { (Ok(sig), Ok(pk)) => mldsa87::verify_detached_signature_ctx(sig, &msg, &ctx, pk).is_ok(), _ => false };
            let expected = case["result"].as_str().unwrap() == "valid";
            println!("case={} accepted={} expected={}",case["tcId"],accepted,expected);
            if accepted != expected {failures += 1;}
        }
    }
    println!("mismatches={failures}");
    if failures > 0 {std::process::exit(1);}
}
