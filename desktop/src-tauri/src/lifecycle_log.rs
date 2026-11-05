//! Independent of WebView/HTTP: survives a paused renderer and pre-server startup.
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::Instant,
};
use serde_json::{json, Value};

const MAX_BYTES: u64 = 512 * 1024;
static LOG: OnceLock<Mutex<NativeLog>> = OnceLock::new();

struct NativeLog {
    path: PathBuf,
    started: Instant,
}

impl NativeLog {
    fn write(&self, stage: &'static str, details: Value) -> io::Result<()> {
        let path = &self.path;
        let previous = path.with_extension("jsonl.1");
        if path.is_symlink() || previous.is_symlink() {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        if fs::metadata(path).is_ok_and(|meta| meta.len() >= MAX_BYTES) {
            if previous.exists() {
                fs::remove_file(&previous)?;
            }
            fs::rename(path, previous)?;
        }
        let message = format!("Native lifecycle: stage={stage} elapsed_ms={} details={details}", self.started.elapsed().as_millis());
        let entry = json!({"timestamp":chrono::Utc::now().to_rfc3339(), "level":"INFO", "logger":"desktop.native", "message":message});
        writeln!(OpenOptions::new().create(true).append(true).open(path)?, "{entry}")
    }
}

pub fn init(profile: &Path) {
    let directory = profile.join("logs");
    if fs::create_dir_all(&directory).is_ok() {
        let _ = LOG.set(Mutex::new(NativeLog {
            path: directory.join("searchcar-native.jsonl"),
            started: Instant::now(),
        }));
    }
}

pub fn record(stage: &'static str, details: Value) {
    if let Some(log) = LOG.get() {
        if let Ok(log) = log.lock() {
            if log.write(stage, details).is_err() {
                eprintln!("Native lifecycle log unavailable");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn logs_valid_json_rotates_and_never_follows_a_symlink() {
        let directory = std::env::temp_dir().join(format!("searchcar-native-test-{}",rand::random::<u64>()));
        fs::create_dir(&directory).unwrap();
        let log = NativeLog {path:directory.join("searchcar-native.jsonl"),started:Instant::now()};
        log.write("menu_exit",json!({"focused":true})).unwrap();
        let entry: Value = serde_json::from_str(&fs::read_to_string(&log.path).unwrap()).unwrap();
        assert!(entry["message"].as_str().unwrap().contains("stage=menu_exit"));
        fs::write(&log.path, vec![b'x';MAX_BYTES as usize]).unwrap();
        log.write("health_snapshot",json!({"ready":true})).unwrap();
        let previous = log.path.with_extension("jsonl.1");
        assert_eq!(fs::metadata(&previous).unwrap().len(),MAX_BYTES);
        assert!(fs::metadata(&log.path).unwrap().len()<1024);
        fs::remove_file(&log.path).unwrap();
        #[cfg(unix)] {
            std::os::unix::fs::symlink(&previous,&log.path).unwrap();
            assert!(log.write("menu_exit",json!({})).is_err());
            fs::remove_file(&log.path).unwrap();
        }
        fs::remove_file(previous).unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
