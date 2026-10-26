fn main() {
    println!("cargo:rerun-if-env-changed=SEARCHCAR_SOURCE_TEST_MODE");
    println!("cargo:rerun-if-env-changed=SEARCHCAR_TELEGRAM_DELIVERY_PREVIEW");
    tauri_build::build()
}
