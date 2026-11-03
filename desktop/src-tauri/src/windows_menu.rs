// Only the main window owns this hook. Recovery runs after the native menu loop,
// never inside a WebView callback and never by simulating a mouse click.
#[derive(Default)]
struct MenuResume {
    pending: bool,
}

impl MenuResume {
    fn queue(&mut self) -> bool {
        !std::mem::replace(&mut self.pending, true)
    }

    fn take(&mut self, foreground: bool, minimized: bool) -> bool {
        std::mem::take(&mut self.pending) && foreground && !minimized
    }
}

#[cfg(windows)]
mod native {
    use super::MenuResume;
    use std::ffi::c_void;
    use tauri::WebviewWindow;
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2Controller, COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC,
    };

    type Hwnd = *mut c_void;
    type Subclass = unsafe extern "system" fn(Hwnd, u32, usize, isize, usize, usize) -> isize;
    const SUBCLASS_ID: usize = 0x53434d52;
    const WM_EXITMENULOOP: u32 = 0x0212;
    const WM_NCDESTROY: u32 = 0x0082;

    #[link(name = "comctl32")]
    extern "system" {
        fn SetWindowSubclass(hwnd: Hwnd, callback: Option<Subclass>, id: usize, data: usize) -> i32;
        fn RemoveWindowSubclass(hwnd: Hwnd, callback: Option<Subclass>, id: usize) -> i32;
        fn DefSubclassProc(hwnd: Hwnd, message: u32, wparam: usize, lparam: isize) -> isize;
    }
    #[link(name = "user32")]
    extern "system" {
        fn RegisterWindowMessageW(name: *const u16) -> u32;
        fn PostMessageW(hwnd: Hwnd, message: u32, wparam: usize, lparam: isize) -> i32;
        fn GetForegroundWindow() -> Hwnd;
        fn IsIconic(hwnd: Hwnd) -> i32;
    }

    struct Hook {
        window: WebviewWindow,
        controller: ICoreWebView2Controller,
        resume_message: u32,
        resume: MenuResume,
    }

    unsafe extern "system" fn procedure(
        hwnd: Hwnd, message: u32, wparam: usize, lparam: isize, id: usize, data: usize,
    ) -> isize {
        if message == WM_NCDESTROY {
            RemoveWindowSubclass(hwnd, Some(procedure), id);
            let result = DefSubclassProc(hwnd, message, wparam, lparam);
            drop(Box::from_raw(data as *mut Hook));
            return result;
        }
        // The subclass and COM controller are installed, used and dropped on this UI thread.
        let pointer = data as *mut Hook;
        let resume_message = (*pointer).resume_message;
        if message == WM_EXITMENULOOP && (*pointer).resume.queue() {
            if PostMessageW(hwnd, resume_message, 0, 0) == 0 {
                (*pointer).resume.pending = false;
                eprintln!("Could not queue native menu recovery");
            }
        } else if message == resume_message {
            let recovery = {
                let hook = &mut *pointer;
                hook.resume.take(GetForegroundWindow() == hwnd, IsIconic(hwnd) != 0)
                    .then(|| (hook.controller.clone(), hook.window.clone()))
            };
            // MoveFocus can re-enter window processing: hold no borrow of Hook across it.
            if let Some((controller, window)) = recovery {
                if let Err(error) = controller.MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC) {
                    eprintln!("Could not restore WebView focus after native menu: {error}");
                }
                if let Err(error) = window.eval("window.dispatchEvent(new Event('searchcar:menu-resume'));") {
                    eprintln!("Could not notify UI after native menu: {error}");
                }
            }
            return 0;
        }
        DefSubclassProc(hwnd, message, wparam, lparam)
    }

    pub fn install(window: &WebviewWindow) {
        let owned = window.clone();
        if let Err(error) = window.with_webview(move |webview| unsafe {
            let Ok(hwnd) = owned.hwnd() else {
                eprintln!("Could not get main window for native menu recovery");
                return;
            };
            let name: Vec<u16> = "SearchCar.MenuResume.v1\0".encode_utf16().collect();
            let resume_message = RegisterWindowMessageW(name.as_ptr());
            if resume_message == 0 {
                eprintln!("Could not register native menu recovery message");
                return;
            }
            let hook = Box::into_raw(Box::new(Hook {
                window: owned, controller: webview.controller(), resume_message,
                resume: MenuResume::default(),
            }));
            if SetWindowSubclass(hwnd.0, Some(procedure), SUBCLASS_ID, hook as usize) == 0 {
                drop(Box::from_raw(hook));
                eprintln!("Could not install native menu recovery");
            }
        }) {
            eprintln!("Could not schedule native menu recovery installation: {error}");
        }
    }
}

#[cfg(windows)]
pub use native::install;

#[cfg(test)]
mod tests {
    use super::MenuResume;

    #[test]
    fn menu_notifications_coalesce_and_recover_once() {
        let mut state = MenuResume::default();
        assert!(!state.take(true, false));
        assert!(state.queue());
        assert!(!state.queue());
        assert!(state.take(true, false));
        assert!(!state.take(true, false));
        assert!(state.queue());
        assert!(state.take(true, false));
    }

    #[test]
    fn closing_or_minimizing_never_steals_focus_and_consumes_the_notification() {
        for (foreground, minimized) in [(false, false), (true, true), (false, true)] {
            let mut state = MenuResume::default();
            assert!(state.queue());
            assert!(!state.take(foreground, minimized));
            assert!(!state.take(true, false));
            assert!(state.queue());
        }
    }
}
