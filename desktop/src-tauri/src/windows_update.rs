use std::{ffi::OsString, path::Path};

pub fn install_directory_argument(executable: &Path) -> Result<OsString, String> {
    let directory = executable
        .parent()
        .filter(|directory| directory.is_absolute())
        .and_then(Path::to_str)
        .ok_or("Executable has no absolute Unicode parent directory")?;
    directory_argument(directory)
}

fn directory_argument(directory: &str) -> Result<OsString, String> {
    // current_exe may return an extended-length Windows path; NSIS expects
    // an ordinary drive/UNC path. Spaces must NOT be surrounded by quotes.
    let directory = if let Some(unc) = directory.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        directory.strip_prefix(r"\\?\").unwrap_or(directory).to_string()
    };
    if directory.is_empty() || directory.contains(['"', '\r', '\n', '\0']) {
        return Err("Executable directory cannot be passed to NSIS".to_string());
    }
    Ok(OsString::from(format!("/D={directory}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nsis_directory_preserves_spaces_unicode_and_network_paths() {
        for (directory, expected) in [
            (r"C:\Users\Serhii\SearchCar Desktop", r"/D=C:\Users\Serhii\SearchCar Desktop"),
            (r"\\?\C:\Users\Сергій\SearchCar Desktop", r"/D=C:\Users\Сергій\SearchCar Desktop"),
            (r"\\?\UNC\server\apps\SearchCar Desktop", r"/D=\\server\apps\SearchCar Desktop"),
        ] {
            assert_eq!(directory_argument(directory).unwrap(), OsString::from(expected));
        }
        for invalid in ["", "C:\\bad\" /S", "C:\\bad\npath", "C:\\bad\0path"] {
            assert!(directory_argument(invalid).is_err());
        }
    }

    #[test]
    fn installer_uses_executable_parent_and_rejects_relative_paths() {
        let executable = std::env::current_exe().unwrap();
        assert_eq!(
            install_directory_argument(&executable).unwrap(),
            directory_argument(executable.parent().unwrap().to_str().unwrap()).unwrap()
        );
        assert!(install_directory_argument(Path::new("searchcar-desktop.exe")).is_err());
    }
}
