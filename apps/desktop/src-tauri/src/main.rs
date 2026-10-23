#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{HashMap, HashSet, VecDeque},
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

#[cfg(target_os = "macos")]
use objc2::MainThreadMarker;
#[cfg(target_os = "macos")]
use objc2_app_kit::{NSModalResponseOK, NSOpenPanel};

const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);
const RESTART_BACKOFF: [Duration; MAX_RESTARTS] = [
    Duration::from_millis(250),
    Duration::from_secs(1),
    Duration::from_secs(2),
];
const MAX_DIAGNOSTIC_ENTRY_BYTES: usize = 10 * 1024 * 1024;
const MAX_DIAGNOSTIC_TOTAL_BYTES: usize = 32 * 1024 * 1024;
const MAX_DIAGNOSTIC_LOG_BYTES: usize = 256 * 1024;
static LOGO_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
}

struct WorkerSupervisor {
    process: Option<WorkerProcess>,
    restart_history: VecDeque<Instant>,
    active_task_id: Option<String>,
    preview_roots: Vec<PathBuf>,
    shutting_down: bool,
}

struct WorkerState(Arc<Mutex<WorkerSupervisor>>);

fn canonical_path(path: &str) -> Result<PathBuf, String> {
    PathBuf::from(path)
        .canonicalize()
        .map_err(|error| format!("path is unavailable: {error}"))
}

fn path_within(path: &PathBuf, root: &PathBuf) -> bool {
    path == root || path.starts_with(root)
}

fn remember_preview_root(guard: &mut WorkerSupervisor, path: &str) {
    let Ok(path) = canonical_path(path) else { return };
    let root = if path.is_dir() {
        path
    } else {
        path.parent().map(PathBuf::from).unwrap_or(path)
    };
    if !guard.preview_roots.iter().any(|known| known == &root) {
        guard.preview_roots.push(root);
    }
}

fn validate_preview_request(guard: &WorkerSupervisor, message: &Value) -> Result<(), String> {
    let payload = message
        .get("payload")
        .and_then(Value::as_object)
        .ok_or_else(|| "preview payload is required".to_string())?;
    let path = payload
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| "preview path is required".to_string())
        .and_then(canonical_path)?;
    let roots = payload
        .get("allowed_roots")
        .and_then(Value::as_array)
        .ok_or_else(|| "preview scope is required".to_string())?;
    if roots.is_empty() {
        return Err("preview scope is required".to_string());
    }
    let mut matched = false;
    for value in roots {
        let root = value
            .as_str()
            .ok_or_else(|| "preview scope must contain paths".to_string())
            .and_then(canonical_path)?;
        if !guard.preview_roots.iter().any(|known| path_within(&root, known)) {
            return Err("preview scope was not selected by the current task".to_string());
        }
        matched |= path_within(&path, &root);
    }
    matched
        .then_some(())
        .ok_or_else(|| "preview path is outside the allowed scope".to_string())
}

#[cfg(target_os = "macos")]
fn native_open_panel(app: &AppHandle, choose_directory: bool) -> Result<Option<String>, String> {
    let (sender, receiver) = std::sync::mpsc::sync_channel(1);
    app.run_on_main_thread(move || {
        let result = (|| {
            let marker = MainThreadMarker::new().ok_or_else(|| "主线程不可用".to_string())?;
            let panel = NSOpenPanel::openPanel(marker);
            panel.setCanChooseDirectories(choose_directory);
            panel.setCanChooseFiles(!choose_directory);
            panel.setAllowsMultipleSelection(false);
            if panel.runModal() != NSModalResponseOK {
                return Ok(None);
            }
            Ok(panel
                .URLs()
                .firstObject()
                .and_then(|url| url.path())
                .map(|path| path.to_string()))
        })();
        let _ = sender.send(result);
    })
    .map_err(|error| format!("native file picker failed: {error}"))?;
    receiver
        .recv_timeout(Duration::from_secs(300))
        .map_err(|_| "native file picker timed out".to_string())?
}

#[tauri::command]
fn worker_send(line: String, state: State<'_, WorkerState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|_| "worker state lock failed")?;
    if let Ok(message) = serde_json::from_str::<Value>(&line) {
        let message_type = message.get("type").and_then(Value::as_str);
        let request_id = message
            .get("request_id")
            .and_then(Value::as_str)
            .map(str::to_owned);
        if message_type == Some("read_preview") {
            validate_preview_request(&guard, &message)?;
        }
        if matches!(message_type, Some("start_generation" | "classify_materials" | "retry_failed")) {
            guard.active_task_id = request_id;
            if let Some(root) = message
                .get("payload")
                .and_then(Value::as_object)
                .and_then(|payload| payload.get("product_root"))
                .and_then(Value::as_str)
            {
                remember_preview_root(&mut guard, root);
            }
        } else if message_type == Some("shutdown") {
            guard.shutting_down = true;
        }
    }
    let process = guard
        .process
        .as_mut()
        .ok_or_else(|| "worker is not running".to_string())?;
    process
        .stdin
        .write_all(line.as_bytes())
        .and_then(|_| process.stdin.write_all(b"\n"))
        .and_then(|_| process.stdin.flush())
        .map_err(|error| format!("worker write failed: {error}"))
}

#[tauri::command]
fn pick_directory(app: AppHandle, state: State<'_, WorkerState>) -> Result<Option<String>, String> {
    let remember = |path: Option<String>| {
        if let Some(path) = &path {
            if let Ok(mut guard) = state.0.lock() {
                remember_preview_root(&mut guard, path);
            }
        }
        path
    };
    #[cfg(target_os = "macos")]
    {
        return Ok(remember(native_open_panel(&app, true)?));
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(remember(windows_dialog("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")?));
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    { Ok(None) }
}

fn contains_onnx_model(directory: &Path) -> bool {
    std::fs::read_dir(directory)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .any(|entry| {
            let path = entry.path();
            path.is_file()
                && path
                    .extension()
                    .map(|extension| extension.to_string_lossy().eq_ignore_ascii_case("onnx"))
                    .unwrap_or(false)
        })
}

fn recognized_model_directory(candidate: &Path) -> Option<PathBuf> {
    if !candidate.is_dir() {
        return None;
    }
    ["pose", "segmentation", "detection"]
        .iter()
        .any(|purpose| contains_onnx_model(&candidate.join(purpose)))
        .then(|| candidate.to_path_buf())
}

fn discover_models_under(home: &Path) -> Option<PathBuf> {
    ["Documents", "文档", "文稿", ""]
        .iter()
        .map(|directory| {
            if directory.is_empty() {
                home.join("models")
            } else {
                home.join(directory).join("models")
            }
        })
        .find_map(|candidate| recognized_model_directory(&candidate))
}

fn push_unique_path(paths: &mut Vec<PathBuf>, path: PathBuf) {
    if !paths.iter().any(|known| known == &path) {
        paths.push(path);
    }
}

fn discover_model_directories() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for variable in ["HOME", "USERPROFILE"] {
        if let Some(home) = std::env::var_os(variable).map(PathBuf::from) {
            for directory in ["Documents", "文档", "文稿", "models"] {
                let candidate = if directory == "models" {
                    home.join(directory)
                } else {
                    home.join(directory).join("models")
                };
                push_unique_path(&mut candidates, candidate);
            }
        }
    }
    if let Some(local_app_data) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) {
        push_unique_path(
            &mut candidates,
            local_app_data.join("PixelFlow").join("models"),
        );
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(parent) = executable.parent() {
            push_unique_path(&mut candidates, parent.join("models"));
            push_unique_path(&mut candidates, parent.join("resources").join("models"));
        }
    }
    candidates
}

/// Return the conventional external model directory when it contains at least
/// one recognized model file.  The UI still probes each model and keeps manual
/// directory selection available; this command only supplies a safe default.
#[tauri::command]
fn discover_models_directory() -> Result<Option<String>, String> {
    Ok(discover_model_directories()
        .iter()
        .find_map(|candidate| recognized_model_directory(candidate))
        .map(|path| path.to_string_lossy().into_owned()))
}

#[tauri::command]
fn pick_file(app: AppHandle, key: String, state: State<'_, WorkerState>) -> Result<Option<String>, String> {
    #[cfg(target_os = "macos")]
    {
        if let Some(path) = native_open_panel(&app, false)? {
            return persist_picked_logo(&app, &state, &key, &path).map(Some);
        }
        return Ok(None);
    }
    #[cfg(target_os = "windows")]
    {
        let path = windows_dialog("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Filter = 'PNG 图片 (*.png)|*.png'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }")?;
        return path
            .map(|path| persist_picked_logo(&app, &state, &key, &path))
            .transpose();
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    { Ok(None) }
}

fn persist_picked_logo(
    app: &AppHandle,
    state: &State<'_, WorkerState>,
    key: &str,
    source: &str,
) -> Result<String, String> {
    let brand_assets = brand_assets_dir(app)?;
    let path = persist_logo_file(Path::new(source), &brand_assets, key)?;
    let path = path.to_string_lossy().into_owned();
    let mut guard = state.0.lock().map_err(|_| "worker state lock failed")?;
    remember_preview_root(&mut guard, &path);
    Ok(path)
}

fn logo_dimensions(key: &str) -> Option<(u32, u32)> {
    match key {
        "logo_square_dark" | "logo_square_light" => Some((1440, 1440)),
        "logo_tall_dark" | "logo_tall_light" => Some((1440, 1920)),
        _ => None,
    }
}

fn validate_logo_bytes(bytes: &[u8], key: &str) -> Result<(), String> {
    let expected = logo_dimensions(key).ok_or_else(|| "unknown logo slot".to_string())?;
    if bytes.len() < 26 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" || &bytes[12..16] != b"IHDR" {
        return Err("Logo 必须是可读取的 PNG".to_string());
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
    if (width, height) != expected { return Err(format!("Logo 画布尺寸必须为 {} × {}", expected.0, expected.1)); }
    if bytes[25] != 6 { return Err("Logo 必须包含 RGBA 透明通道".to_string()); }
    Ok(())
}

fn read_logo_bytes(path: &Path, key: &str) -> Result<Vec<u8>, String> {
    let bytes = fs::read(path).map_err(|error| format!("Logo read failed: {error}"))?;
    validate_logo_bytes(&bytes, key)?;
    Ok(bytes)
}

fn brand_assets_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .local_data_dir()
        .map(|path| path.join("PixelFlow").join("brand-assets"))
        .map_err(|error| format!("local data directory is unavailable: {error}"))
}

fn logo_temp_path(brand_assets: &Path) -> PathBuf {
    brand_assets.join(format!(
        ".pixelflow-logo-{}-{}.tmp",
        std::process::id(),
        LOGO_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ))
}

fn store_logo_bytes(brand_assets: &Path, key: &str, bytes: &[u8]) -> Result<PathBuf, String> {
    validate_logo_bytes(bytes, key)?;
    fs::create_dir_all(brand_assets)
        .map_err(|error| format!("Logo directory create failed: {error}"))?;
    let digest = format!("{:x}", Sha256::digest(bytes));
    let destination = brand_assets.join(format!("{digest}.png"));
    if destination.is_file() && read_logo_bytes(&destination, key).is_ok() {
        return Ok(destination);
    }

    let temporary = logo_temp_path(brand_assets);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|error| format!("Logo temporary file create failed: {error}"))?;
    if let Err(error) = file.write_all(bytes).and_then(|_| file.sync_all()) {
        drop(file);
        let _ = fs::remove_file(&temporary);
        return Err(format!("Logo write failed: {error}"));
    }
    drop(file);
    match fs::rename(&temporary, &destination) {
        Ok(()) => Ok(destination),
        Err(error) => {
            let _ = fs::remove_file(&temporary);
            if destination.is_file() && read_logo_bytes(&destination, key).is_ok() {
                Ok(destination)
            } else {
                Err(format!("Logo atomic rename failed: {error}"))
            }
        }
    }
}

fn persist_logo_file(source: &Path, brand_assets: &Path, key: &str) -> Result<PathBuf, String> {
    logo_dimensions(key).ok_or_else(|| "unknown logo slot".to_string())?;
    let bytes = read_logo_bytes(source, key)?;
    store_logo_bytes(brand_assets, key, &bytes)
}

fn is_brand_asset_path(path: &Path, brand_assets: &Path) -> bool {
    let Ok(path) = path.canonicalize() else { return false };
    let Ok(brand_assets) = brand_assets.canonicalize() else { return false };
    path.parent() == Some(brand_assets.as_path())
}

fn migrate_logo_overrides(
    logo_overrides: &HashMap<String, String>,
    brand_assets: &Path,
) -> HashMap<String, String> {
    let mut authorized = HashMap::new();
    for (key, path) in logo_overrides {
        if logo_dimensions(key).is_none() {
            continue;
        }
        let source = Path::new(path);
        let Ok(bytes) = read_logo_bytes(source, key) else { continue };
        if is_brand_asset_path(source, brand_assets) {
            authorized.insert(key.clone(), path.clone());
        } else if let Ok(path) = store_logo_bytes(brand_assets, key, &bytes) {
            authorized.insert(key.clone(), path.to_string_lossy().into_owned());
        } else {
            // Keep the valid legacy path usable for preview; the next startup retries the copy.
            authorized.insert(key.clone(), path.clone());
        }
    }
    authorized
}

fn authorize_logo_preview_roots(
    guard: &mut WorkerSupervisor,
    logo_overrides: &HashMap<String, String>,
) {
    for path in logo_overrides.values() {
        remember_preview_root(guard, path);
    }
}

#[tauri::command]
fn authorize_logo_previews(
    app: AppHandle,
    logo_overrides: HashMap<String, String>,
    state: State<'_, WorkerState>,
) -> Result<HashMap<String, String>, String> {
    let brand_assets = brand_assets_dir(&app)?;
    let authorized = migrate_logo_overrides(&logo_overrides, &brand_assets);
    let mut guard = state.0.lock().map_err(|_| "worker state lock failed")?;
    authorize_logo_preview_roots(&mut guard, &authorized);
    Ok(authorized)
}

#[cfg(target_os = "windows")]
fn windows_dialog(script: &str) -> Result<Option<String>, String> {
    let script = windows_utf8_script(script);
    let mut command = Command::new("powershell");
    command
        .arg("-NoProfile")
        .arg("-STA")
        .arg("-Command")
        .arg(&script);
    hide_console(&mut command);
    let output = command
        .output()
        .map_err(|error| format!("file dialog failed: {error}"))?;
    if !output.status.success() {
        return Err(format!("file dialog failed: {}", String::from_utf8_lossy(&output.stderr)));
    }
    let path = decode_windows_stdout(&output.stdout)?;
    Ok((!path.is_empty()).then_some(path))
}

#[cfg(any(target_os = "windows", test))]
fn windows_utf8_script(script: &str) -> String {
    format!(
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); {script}"
    )
}

#[cfg(any(target_os = "windows", test))]
fn decode_windows_stdout(output: &[u8]) -> Result<String, String> {
    std::str::from_utf8(output)
        .map(|path| path.trim().to_string())
        .map_err(|error| format!("file dialog returned invalid UTF-8: {error}"))
}

#[tauri::command]
fn choose_save_path(
    default_name: &str,
    prompt: &str,
    windows_filter: &str,
) -> Result<Option<PathBuf>, String> {
    #[cfg(target_os = "macos")]
    {
        let _ = windows_filter;
        let safe_name = default_name
            .replace('\\', "/")
            .replace('"', "'")
            .replace('\n', " ")
            .replace('\r', " ");
        let safe_prompt = prompt
            .replace('\\', "/")
            .replace('"', "'")
            .replace('\n', " ")
            .replace('\r', " ");
        let script = format!(
            "POSIX path of (choose file name with prompt \"{safe_prompt}\" default name \"{safe_name}\")"
        );
        let output = Command::new("osascript")
            .args(["-e", &script])
            .output()
            .map_err(|error| format!("save dialog failed: {error}"))?;
        if !output.status.success() {
            return Ok(None);
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        return Ok((!path.is_empty()).then_some(PathBuf::from(path)));
    }
    #[cfg(target_os = "windows")]
    {
        let script = "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.SaveFileDialog; $d.FileName = $env:PIXELFLOW_FILE_NAME; $d.Filter = $env:PIXELFLOW_FILE_FILTER; $d.Title = $env:PIXELFLOW_DIALOG_TITLE; if ($d.ShowDialog() -eq 'OK') { $d.FileName }";
        let script = windows_utf8_script(script);
        let mut command = Command::new("powershell");
        command
            .arg("-NoProfile")
            .arg("-STA")
            .arg("-Command")
            .arg(&script)
            .env("PIXELFLOW_FILE_NAME", default_name)
            .env("PIXELFLOW_FILE_FILTER", windows_filter)
            .env("PIXELFLOW_DIALOG_TITLE", prompt);
        hide_console(&mut command);
        let output = command
            .output()
            .map_err(|error| format!("save dialog failed: {error}"))?;
        if !output.status.success() {
            return Err(format!("save dialog failed: {}", String::from_utf8_lossy(&output.stderr)));
        }
        let path = decode_windows_stdout(&output.stdout)?;
        return Ok((!path.is_empty()).then_some(PathBuf::from(path)));
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        let _ = (default_name, prompt, windows_filter);
        Ok(None)
    }
}

#[tauri::command]
fn save_text_file(default_name: String, content: String) -> Result<Option<String>, String> {
    let Some(path) = choose_save_path(
        &default_name,
        "导出失败报告",
        "Text files (*.txt)|*.txt",
    )? else {
        return Ok(None);
    };
    std::fs::write(&path, content).map_err(|error| format!("write report failed: {error}"))?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

fn crc32(bytes: &[u8]) -> u32 {
    let mut checksum = u32::MAX;
    for byte in bytes {
        checksum ^= u32::from(*byte);
        for _ in 0..8 {
            let mask = 0u32.wrapping_sub(checksum & 1);
            checksum = (checksum >> 1) ^ (0xedb8_8320 & mask);
        }
    }
    !checksum
}

fn push_u16(buffer: &mut Vec<u8>, value: u16) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn push_u32(buffer: &mut Vec<u8>, value: u32) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn validate_zip_entry_name(raw: &str) -> Result<String, String> {
    let name = raw.replace('\\', "/");
    if name.is_empty()
        || name.starts_with('/')
        || name.contains(':')
        || name.split('/').any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err("diagnostic entry name is invalid".to_string());
    }
    if name.len() > usize::from(u16::MAX) {
        return Err("diagnostic entry name is too long".to_string());
    }
    Ok(name)
}

fn build_zip_archive(entries: Vec<(String, Vec<u8>)>) -> Result<Vec<u8>, String> {
    if entries.len() > usize::from(u16::MAX) {
        return Err("too many diagnostic entries".to_string());
    }
    let entry_count = entries.len();
    let mut archive = Vec::new();
    let mut central_directory = Vec::new();
    let mut names = HashSet::new();
    let mut total_size = 0usize;

    for (raw_name, content) in entries {
        let name = validate_zip_entry_name(&raw_name)?;
        if !names.insert(name.clone()) {
            return Err(format!("duplicate diagnostic entry: {name}"));
        }
        if content.len() > MAX_DIAGNOSTIC_ENTRY_BYTES {
            return Err(format!("diagnostic entry is too large: {name}"));
        }
        total_size = total_size
            .checked_add(content.len())
            .ok_or_else(|| "diagnostic archive is too large".to_string())?;
        if total_size > MAX_DIAGNOSTIC_TOTAL_BYTES {
            return Err("diagnostic archive is too large".to_string());
        }
        let name_bytes = name.as_bytes();
        let size = u32::try_from(content.len())
            .map_err(|_| format!("diagnostic entry is too large: {name}"))?;
        let checksum = crc32(&content);
        let offset = u32::try_from(archive.len())
            .map_err(|_| "diagnostic archive is too large".to_string())?;

        archive.extend_from_slice(b"PK\x03\x04");
        push_u16(&mut archive, 20);
        push_u16(&mut archive, 0);
        push_u16(&mut archive, 0);
        push_u16(&mut archive, 0);
        push_u16(&mut archive, 0);
        push_u32(&mut archive, checksum);
        push_u32(&mut archive, size);
        push_u32(&mut archive, size);
        push_u16(&mut archive, name_bytes.len() as u16);
        push_u16(&mut archive, 0);
        archive.extend_from_slice(name_bytes);
        archive.extend_from_slice(&content);

        central_directory.extend_from_slice(b"PK\x01\x02");
        push_u16(&mut central_directory, 20);
        push_u16(&mut central_directory, 20);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u32(&mut central_directory, checksum);
        push_u32(&mut central_directory, size);
        push_u32(&mut central_directory, size);
        push_u16(&mut central_directory, name_bytes.len() as u16);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u16(&mut central_directory, 0);
        push_u32(&mut central_directory, 0);
        push_u32(&mut central_directory, offset);
        central_directory.extend_from_slice(name_bytes);
    }

    let central_offset = u32::try_from(archive.len())
        .map_err(|_| "diagnostic archive is too large".to_string())?;
    let central_size = u32::try_from(central_directory.len())
        .map_err(|_| "diagnostic archive is too large".to_string())?;
    archive.extend_from_slice(&central_directory);
    archive.extend_from_slice(b"PK\x05\x06");
    push_u16(&mut archive, 0);
    push_u16(&mut archive, 0);
    push_u16(&mut archive, entry_count as u16);
    push_u16(&mut archive, entry_count as u16);
    push_u32(&mut archive, central_size);
    push_u32(&mut archive, central_offset);
    push_u16(&mut archive, 0);
    Ok(archive)
}

fn recent_worker_log() -> Vec<u8> {
    match std::fs::read(worker_log_path()) {
        Ok(bytes) => bytes[bytes.len().saturating_sub(MAX_DIAGNOSTIC_LOG_BYTES)..].to_vec(),
        Err(error) => format!("worker log unavailable: {error}\n").into_bytes(),
    }
}

#[tauri::command]
fn export_diagnostics(default_name: String, entries: Vec<Value>) -> Result<Option<String>, String> {
    let mut archive_entries = Vec::with_capacity(entries.len() + 1);
    for entry in entries {
        let name = entry
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| "diagnostic entry name is required".to_string())?
            .to_string();
        let content = entry
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("diagnostic entry content is required: {name}"))?;
        archive_entries.push((name, content.as_bytes().to_vec()));
    }
    archive_entries.push(("worker.log".to_string(), recent_worker_log()));
    let archive = build_zip_archive(archive_entries)?;
    let Some(path) = choose_save_path(
        &default_name,
        "导出诊断包",
        "ZIP files (*.zip)|*.zip",
    )? else {
        return Ok(None);
    };
    std::fs::write(&path, archive).map_err(|error| format!("write diagnostics failed: {error}"))?;
    Ok(Some(path.to_string_lossy().into_owned()))
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    if !path.is_dir() {
        return Err(format!("directory not found: {}", path.display()));
    }
    #[cfg(target_os = "macos")]
    let status = Command::new("open").arg(&path).status();
    #[cfg(target_os = "windows")]
    let status = Command::new("explorer").arg(&path).status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = Command::new("xdg-open").arg(&path).status();
    status
        .map_err(|error| format!("open directory failed: {error}"))
        .and_then(|result| {
            result
                .success()
                .then_some(())
                .ok_or_else(|| "open directory failed".to_string())
        })
}

fn worker_log_path() -> PathBuf {
    #[cfg(target_os = "windows")]
    return std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("PixelFlow/logs/worker.log");
    #[cfg(target_os = "macos")]
    return std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Library/Logs/PixelFlow/worker.log");
    #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
    {
        let root = std::env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/state")))
            .unwrap_or_else(|| PathBuf::from("."));
        root.join("PixelFlow").join("logs").join("worker.log")
    }
}

fn open_worker_log() -> Result<File, String> {
    let path = worker_log_path();
    let parent = path
        .parent()
        .ok_or_else(|| "worker log directory is unavailable".to_string())?;
    std::fs::create_dir_all(parent).map_err(|error| format!("worker log directory failed: {error}"))?;
    const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;
    if path.metadata().map(|metadata| metadata.len() > MAX_LOG_BYTES).unwrap_or(false) {
        let rotated = path.with_extension("log.1");
        let _ = std::fs::remove_file(&rotated);
        let _ = std::fs::rename(&path, rotated);
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|error| format!("worker log open failed: {error}"))
}

#[cfg(target_os = "windows")]
fn hide_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    command.creation_flags(0x08000000);
}

#[cfg(not(target_os = "windows"))]
fn hide_console(_command: &mut Command) {}

fn worker_exit_payload(message: &str, task_id: Option<String>, retryable: bool) -> Value {
    serde_json::json!({
        "message": message,
        "task_id": task_id,
        "retryable": retryable,
    })
}

fn emit_worker_exit(app: &AppHandle, message: &str, task_id: Option<String>, retryable: bool) {
    let _ = app.emit("worker-exit", worker_exit_payload(message, task_id, retryable));
}

fn reserve_restart_slot(
    history: &mut VecDeque<Instant>,
    shutting_down: bool,
    now: Instant,
) -> Option<Duration> {
    if shutting_down {
        return None;
    }
    history.retain(|started| now.duration_since(*started) < RESTART_WINDOW);
    if history.len() >= MAX_RESTARTS {
        return None;
    }
    let attempt = history.len();
    history.push_back(now);
    Some(RESTART_BACKOFF[attempt])
}

fn reserve_restart(app: &AppHandle) -> Option<Duration> {
    let state = app.state::<WorkerState>();
    let mut guard = state.0.lock().ok()?;
    let now = Instant::now();
    let shutting_down = guard.shutting_down;
    reserve_restart_slot(&mut guard.restart_history, shutting_down, now)
}

fn restart_worker(app: &AppHandle, task_id: Option<String>) {
    let Some(delay) = reserve_restart(app) else {
        emit_worker_exit(app, "Worker 无法启动；已停止自动重启，请查看日志", task_id, false);
        return;
    };
    thread::sleep(delay);
    match spawn_worker(app) {
        Ok(next) => {
            let state = app.state::<WorkerState>();
            if let Ok(mut guard) = state.0.lock() {
                if guard.shutting_down {
                    drop(guard);
                    let mut next = next;
                    let _ = next.child.kill();
                    let _ = next.child.wait();
                } else {
                    guard.process = Some(next);
                }
            };
        }
        Err(error) => {
            emit_worker_exit(app, &format!("Worker 重启失败：{error}"), task_id.clone(), true);
            restart_worker(app, task_id);
        }
    }
}

fn worker_dir() -> PathBuf {
    std::env::var_os("PIXELFLOW_WORKER_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("../../worker"))
}

fn spawn_worker(app: &AppHandle) -> Result<WorkerProcess, String> {
    let bundled_worker = app
        .path()
        .resource_dir()
        .ok()
        .and_then(|root| {
            let resource = root.join("pixelflow-worker");
            if resource.is_file() {
                Some(resource)
            } else {
                let executable = resource.join("pixelflow-worker");
                if executable.is_file() {
                    Some(executable)
                } else {
                    #[cfg(target_os = "windows")]
                    {
                        let windows_executable = resource.join("pixelflow-worker.exe");
                        windows_executable.is_file().then_some(windows_executable)
                    }
                    #[cfg(not(target_os = "windows"))]
                    {
                        None
                    }
                }
            }
        });
    let (program, args, working_dir) = match bundled_worker {
        Some(path) => (path.into_os_string(), Vec::<OsString>::new(), None),
        None => (
            std::env::var_os("PIXELFLOW_PYTHON").unwrap_or_else(|| "python3".into()),
            vec![OsString::from("-m"), OsString::from("worker_entry")],
            Some(worker_dir()),
        ),
    };
    let mut command = Command::new(program);
    command.args(args);
    if let Some(directory) = working_dir {
        command.current_dir(directory);
    }
    hide_console(&mut command);
    let stderr = open_worker_log()?;
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|error| format!("worker start failed: {error}"))?;
    let stdout = child.stdout.take().ok_or_else(|| "worker stdout unavailable".to_string())?;
    let app = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut read_error = None;
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    let line = line.trim_end_matches(&['\r', '\n'][..]).to_string();
                    if let Ok(message) = serde_json::from_str::<Value>(&line) {
                        let request_id = message.get("request_id").and_then(Value::as_str);
                        let event_type = message.get("type").and_then(Value::as_str);
                        let state = app.state::<WorkerState>();
                        if let Ok(mut guard) = state.0.lock() {
                            if matches!(event_type, Some("done" | "cancelled" | "error"))
                                && guard.active_task_id.as_deref() == request_id
                            {
                                guard.active_task_id = None;
                            }
                        };
                    }
                    let _ = app.emit("worker-line", line);
                }
                Err(error) => {
                    let message = format!("Worker 输出读取失败：{error}");
                    eprintln!("{message}");
                    read_error = Some(message);
                    break;
                }
            }
        }
        let state = app.state::<WorkerState>();
        let (task_id, should_restart) = if let Ok(mut guard) = state.0.lock() {
            let task_id = guard.active_task_id.take();
            let old = guard.process.take();
            if let Some(mut old) = old {
                let _ = old.child.kill();
                let _ = old.child.wait();
            }
            (task_id, !guard.shutting_down)
        } else {
            (None, false)
        };
        if should_restart {
            let message = read_error
                .as_deref()
                .unwrap_or("Worker 意外退出；已保留完成的输出，正在重启");
            emit_worker_exit(&app, message, task_id.clone(), true);
            restart_worker(&app, task_id);
        } else if let Some(message) = read_error.as_deref() {
            emit_worker_exit(&app, message, task_id, false);
        }
    });
    let stdin = child.stdin.take().ok_or_else(|| "worker stdin unavailable".to_string())?;
    Ok(WorkerProcess { child, stdin })
}

fn main() {
    let state = WorkerState(Arc::new(Mutex::new(WorkerSupervisor {
        process: None,
        restart_history: VecDeque::new(),
        active_task_id: None,
        preview_roots: Vec::new(),
        shutting_down: false,
    })));
    tauri::Builder::default()
        .manage(state)
        .setup(|app| {
            let process = spawn_worker(app.handle())?;
            let state = app.state::<WorkerState>();
            let mut guard = state.0.lock().map_err(|_| "worker state lock failed")?;
            if guard.process.is_none() {
                guard.process = Some(process);
            } else {
                drop(guard);
                let mut process = process;
                let _ = process.child.kill();
                let _ = process.child.wait();
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_send,
            authorize_logo_previews,
            pick_directory,
            discover_models_directory,
            pick_file,
            save_text_file,
            export_diagnostics,
            open_path
        ])
        .build(tauri::generate_context!())
        .expect("error while building PixelFlow")
        .run(|app, event| {
            if matches!(event, RunEvent::Exit) {
                let state = app.state::<WorkerState>();
                if let Ok(mut guard) = state.0.lock() {
                    guard.shutting_down = true;
                    if let Some(mut process) = guard.process.take() {
                        let _ = process.stdin.write_all(
                            b"{\"protocol\":1,\"request_id\":\"host-shutdown\",\"type\":\"shutdown\",\"payload\":{}}\n",
                        );
                        let _ = process.stdin.flush();
                        let deadline = Instant::now() + Duration::from_secs(10);
                        while Instant::now() < deadline {
                            if process.child.try_wait().ok().flatten().is_some() { break; }
                            thread::sleep(Duration::from_millis(100));
                        }
                        if process.child.try_wait().ok().flatten().is_none() { let _ = process.child.kill(); }
                        let _ = process.child.wait();
                    }
                };
            };
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_logo_dir(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "pixelflow-logo-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    fn logo_png(width: u32, height: u32, marker: u8) -> Vec<u8> {
        let mut png = vec![0_u8; 27];
        png[..8].copy_from_slice(b"\x89PNG\r\n\x1a\n");
        png[12..16].copy_from_slice(b"IHDR");
        png[16..20].copy_from_slice(&width.to_be_bytes());
        png[20..24].copy_from_slice(&height.to_be_bytes());
        png[25] = 6;
        png[26] = marker;
        png
    }

    #[test]
    fn restart_budget_stops_after_three_attempts() {
        let now = Instant::now();
        let mut history = VecDeque::new();

        assert_eq!(reserve_restart_slot(&mut history, false, now), Some(RESTART_BACKOFF[0]));
        assert_eq!(reserve_restart_slot(&mut history, false, now), Some(RESTART_BACKOFF[1]));
        assert_eq!(reserve_restart_slot(&mut history, false, now), Some(RESTART_BACKOFF[2]));
        assert_eq!(reserve_restart_slot(&mut history, false, now), None);
        assert_eq!(history.len(), MAX_RESTARTS);
    }

    #[test]
    fn restart_budget_ignores_expired_attempts_and_shutdown() {
        let now = Instant::now();
        let mut history = VecDeque::from([now - RESTART_WINDOW - Duration::from_secs(1)]);

        assert_eq!(reserve_restart_slot(&mut history, false, now), Some(RESTART_BACKOFF[0]));
        assert_eq!(history.len(), 1);
        assert_eq!(reserve_restart_slot(&mut history, true, now), None);
        assert_eq!(history.len(), 1);
    }

    #[test]
    fn worker_exit_payload_keeps_task_id() {
        let payload = worker_exit_payload("boom", Some("job-7".to_string()), true);
        assert_eq!(payload["task_id"], "job-7");
        assert_eq!(payload["retryable"], true);
    }

    #[test]
    fn persisted_logo_survives_source_removal() {
        let root = temp_logo_dir("survives-removal");
        let source = root.join("desktop-logo.png");
        let brand_assets = root.join("brand-assets");
        let bytes = logo_png(1440, 1440, 1);
        std::fs::write(&source, &bytes).unwrap();

        let persisted =
            persist_logo_file(&source, &brand_assets, "logo_square_dark").unwrap();
        std::fs::remove_file(&source).unwrap();

        assert_eq!(persisted.parent(), Some(brand_assets.as_path()));
        assert_eq!(read_logo_bytes(&persisted, "logo_square_dark").unwrap(), bytes);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn persisted_logo_names_are_content_addressed() {
        let root = temp_logo_dir("content-addressed");
        let brand_assets = root.join("brand-assets");
        let first = root.join("first.png");
        let second = root.join("second.png");
        std::fs::write(&first, logo_png(1440, 1920, 1)).unwrap();
        std::fs::write(&second, logo_png(1440, 1920, 2)).unwrap();

        let first = persist_logo_file(&first, &brand_assets, "logo_tall_dark").unwrap();
        let second = persist_logo_file(&second, &brand_assets, "logo_tall_dark").unwrap();

        assert_ne!(first, second);
        assert!(first.is_file());
        assert!(second.is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn invalid_logo_does_not_leave_a_persistent_copy() {
        let root = temp_logo_dir("invalid-source");
        let source = root.join("invalid.png");
        let brand_assets = root.join("brand-assets");
        std::fs::write(&source, b"not a png").unwrap();

        assert!(persist_logo_file(&source, &brand_assets, "logo_square_light").is_err());
        let persistent_count = std::fs::read_dir(&brand_assets)
            .map(|entries| entries.count())
            .unwrap_or(0);
        assert_eq!(persistent_count, 0);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn logo_migration_keeps_valid_sources_and_drops_missing_paths() {
        let root = temp_logo_dir("migration");
        let brand_assets = root.join("brand-assets");
        let source = root.join("legacy-logo.png");
        let invalid = root.join("invalid.png");
        let missing = root.join("missing.png");
        let bytes = logo_png(1440, 1440, 3);
        std::fs::write(&source, &bytes).unwrap();
        std::fs::write(&invalid, b"not a png").unwrap();
        let overrides = HashMap::from([
            (
                "logo_square_dark".to_string(),
                source.to_string_lossy().into_owned(),
            ),
            (
                "logo_square_light".to_string(),
                invalid.to_string_lossy().into_owned(),
            ),
            (
                "logo_tall_dark".to_string(),
                missing.to_string_lossy().into_owned(),
            ),
        ]);

        let migrated = migrate_logo_overrides(&overrides, &brand_assets);

        assert_eq!(migrated.len(), 1);
        let persisted = Path::new(&migrated["logo_square_dark"]);
        assert_eq!(persisted.parent(), Some(brand_assets.as_path()));
        assert_eq!(read_logo_bytes(persisted, "logo_square_dark").unwrap(), bytes);
        assert!(persisted.is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn logo_migration_keeps_valid_legacy_path_when_copy_fails() {
        let root = temp_logo_dir("copy-failure");
        let brand_assets = root.join("brand-assets");
        let source = root.join("legacy-logo.png");
        std::fs::write(&source, logo_png(1440, 1920, 4)).unwrap();
        std::fs::write(&brand_assets, b"blocks directory creation").unwrap();
        let overrides = HashMap::from([(
            "logo_tall_light".to_string(),
            source.to_string_lossy().into_owned(),
        )]);

        let migrated = migrate_logo_overrides(&overrides, &brand_assets);

        assert_eq!(
            migrated["logo_tall_light"],
            source.to_string_lossy().into_owned()
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn logo_authorization_only_registers_persistent_preview_roots() {
        let root = temp_logo_dir("preview-authorization");
        let brand_assets = root.join("brand-assets");
        let source = root.join("legacy-logo.png");
        std::fs::write(&source, logo_png(1440, 1440, 5)).unwrap();
        let overrides = HashMap::from([(
            "logo_square_dark".to_string(),
            source.to_string_lossy().into_owned(),
        )]);
        let migrated = migrate_logo_overrides(&overrides, &brand_assets);
        let persisted = PathBuf::from(&migrated["logo_square_dark"]);

        let mut guard = WorkerSupervisor {
            process: None,
            restart_history: VecDeque::new(),
            active_task_id: None,
            preview_roots: Vec::new(),
            shutting_down: false,
        };
        let message = serde_json::json!({
            "payload": {
                "path": persisted.to_string_lossy(),
                "allowed_roots": [brand_assets.to_string_lossy()],
            }
        });
        assert!(validate_preview_request(&guard, &message).is_err());

        authorize_logo_preview_roots(&mut guard, &migrated);

        assert!(validate_preview_request(&guard, &message).is_ok());
        let legacy_message = serde_json::json!({
            "payload": {
                "path": source.to_string_lossy(),
                "allowed_roots": [source.parent().unwrap().to_string_lossy()],
            }
        });
        assert!(validate_preview_request(&guard, &legacy_message).is_err());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn windows_dialog_output_is_strict_utf8() {
        assert_eq!(
            decode_windows_stdout("D:\\模型\\models\r\n".as_bytes()).unwrap(),
            "D:\\模型\\models"
        );
        assert!(decode_windows_stdout(&[0xd6, 0xd0]).is_err());
        assert!(windows_utf8_script("$d.SelectedPath").contains("[Console]::OutputEncoding"));
    }

    #[test]
    fn model_discovery_ignores_empty_documents_directory() {
        let root = std::env::temp_dir().join(format!("pixelflow-model-discovery-{}", std::process::id()));
        let models = root.join("Documents").join("models");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&models).unwrap();
        assert!(discover_models_under(&root).is_none());
        std::fs::create_dir_all(models.join("pose")).unwrap();
        std::fs::write(models.join("pose").join("yolo11n-pose.onnx"), b"test").unwrap();
        assert_eq!(discover_models_under(&root).as_deref(), Some(models.as_path()));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn model_discovery_checks_windows_fallback_directories() {
        for (index, relative) in ["models", "文档/models", "文稿/models"].into_iter().enumerate() {
            let root = std::env::temp_dir().join(format!(
                "pixelflow-model-discovery-{}-{}",
                std::process::id(),
                index
            ));
            let models = root.join(relative);
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(models.join("segmentation")).unwrap();
            std::fs::write(models.join("segmentation").join("custom.onnx"), b"test").unwrap();
            assert_eq!(discover_models_under(&root).as_deref(), Some(models.as_path()));
            let _ = std::fs::remove_dir_all(root);
        }
    }

    #[test]
    fn diagnostic_archive_has_zip_records_and_rejects_traversal() {
        let archive = build_zip_archive(vec![("metadata.json".to_string(), b"{}".to_vec())]).unwrap();
        assert_eq!(&archive[..4], b"PK\x03\x04");
        assert!(archive.windows(4).any(|window| window == b"PK\x01\x02"));
        assert!(archive.windows(4).any(|window| window == b"PK\x05\x06"));
        assert!(validate_zip_entry_name("../outside.txt").is_err());
    }
}
