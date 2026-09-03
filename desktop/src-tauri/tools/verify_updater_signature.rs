use base64::{engine::general_purpose::STANDARD, Engine as _};
use minisign_verify::{PublicKey, Signature};
use std::{env, fs, path::Path, process};

fn fail(message: impl std::fmt::Display) -> ! {
    eprintln!("Updater signature verification failed: {message}");
    process::exit(1);
}

fn main() {
    let arguments: Vec<_> = env::args_os().skip(1).collect();
    if arguments.len() != 3 {
        fail("usage: verify_updater_signature <public-key> <artifact> <signature>");
    }
    let public_key_path = Path::new(&arguments[0]);
    let artifact_path = Path::new(&arguments[1]);
    let signature_path = Path::new(&arguments[2]);

    let encoded_key = fs::read_to_string(public_key_path).unwrap_or_else(|error| fail(error));
    let decoded_key = STANDARD
        .decode(encoded_key.trim())
        .unwrap_or_else(|error| fail(error));
    let decoded_key = String::from_utf8(decoded_key).unwrap_or_else(|error| fail(error));
    let public_key = PublicKey::decode(&decoded_key).unwrap_or_else(|error| fail(error));
    let signature = Signature::from_file(signature_path).unwrap_or_else(|error| fail(error));
    let artifact = fs::read(artifact_path).unwrap_or_else(|error| fail(error));
    public_key
        .verify(&artifact, &signature, false)
        .unwrap_or_else(|error| fail(error));
    println!("Updater signature verified: {}", artifact_path.display());
}
