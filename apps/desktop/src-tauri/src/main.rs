#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::{HashSet, VecDeque},
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde_json::Value;
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
const MAX_WORKER_EVENTS_BYTES: u64 = 512 * 1024;
const MAX_WORKER_EVENT_LINE_BYTES: usize = 2048;
const MAX_LAST_TASK_BYTES: usize = 10 * 1024 * 1024;

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

fn discover_models_under(home: &PathBuf) -> Option<PathBuf> {
    let candidate = home.join("Documents").join("models");
    if !candidate.is_dir() {
        return None;
    }
    let recognized = [
        candidate.join("pose").join("yolo11n-pose.onnx"),
        candidate.join("segmentation").join("yolo11n-seg.onnx"),
        candidate.join("detection").join("yolov8m-worldv2-pixelflow.onnx"),
    ];
    if recognized.iter().any(|path| path.is_file()) {
        return Some(candidate);
    }
    None
}

/// Return the conventional external model directory when it contains at least
/// one recognized model file.  The UI still probes each model and keeps manual
/// directory selection available; this command only supplies a safe default.
#[tauri::command]
fn discover_models_directory() -> Result<Option<String>, String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from);
    Ok(home
        .as_ref()
        .and_then(discover_models_under)
        .map(|path| path.to_string_lossy().into_owned()))
}

#[tauri::command]
fn pick_file(app: AppHandle, key: String, state: State<'_, WorkerState>) -> Result<Option<String>, String> {
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
        if let Some(path) = native_open_panel(&app, false)? {
            validate_logo(&path, &key)?;
            return Ok(remember(Some(path)));
        }
        return Ok(None);
    }
    #[cfg(target_os = "windows")]
    {
        let path = windows_dialog("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; $d.Filter = 'PNG 图片 (*.png)|*.png'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }")?;
        if let Some(path) = &path { validate_logo(path, &key)?; }
        return Ok(remember(path));
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    { Ok(None) }
}

#[tauri::command]
fn authorize_logo_preview(path: String, key: String, state: State<'_, WorkerState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|_| "worker state lock failed")?;
    register_logo_preview(&mut guard, &path, &key)
}

fn register_logo_preview(guard: &mut WorkerSupervisor, path: &str, key: &str) -> Result<(), String> {
    validate_logo(path, key)?;
    remember_preview_root(guard, path);
    Ok(())
}

fn validate_logo(path: &str, key: &str) -> Result<(), String> {
    let expected = if key.starts_with("logo_square_") { (1440, 1440) }
        else if key.starts_with("logo_tall_") { (1440, 1920) }
        else { return Err("unknown logo slot".to_string()); };
    let bytes = std::fs::read(path).map_err(|error| format!("Logo read failed: {error}"))?;
    if bytes.len() < 26 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" || &bytes[12..16] != b"IHDR" {
        return Err("Logo 必须是可读取的 PNG".to_string());
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
    if (width, height) != expected { return Err(format!("Logo 画布尺寸必须为 {} × {}", expected.0, expected.1)); }
    if bytes[25] != 6 { return Err("Logo 必须包含 RGBA 透明通道".to_string()); }
    Ok(())
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
        Ok(bytes) => redact_log_bytes(&bytes),
        Err(error) => format!("worker log unavailable: {error}\n").into_bytes(),
    }
}

fn redact_log_bytes(bytes: &[u8]) -> Vec<u8> {
    let text = String::from_utf8_lossy(bytes);
    let redacted = text.lines().map(privacy_text).collect::<Vec<_>>().join("\n") + "\n";
    let bytes = redacted.into_bytes();
    bytes[bytes.len().saturating_sub(MAX_DIAGNOSTIC_LOG_BYTES)..].to_vec()
}

fn recent_log_file(path: PathBuf, name: &str) -> Option<(String, Vec<u8>)> {
    let bytes = std::fs::read(path).ok()?;
    Some((name.to_string(), redact_log_bytes(&bytes)))
}

fn worker_events_path() -> PathBuf {
    worker_log_path().with_file_name("worker-events.jsonl")
}

fn diagnostics_cache_root() -> PathBuf {
    #[cfg(target_os = "macos")]
    return std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
        .join("Library/Caches/PixelFlow/diagnostics");
    #[cfg(not(target_os = "macos"))]
    return std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".cache")))
        .unwrap_or_else(|| PathBuf::from("."))
        .join("PixelFlow/diagnostics");
}

fn safe_diagnostics_path(raw: &str) -> Option<PathBuf> {
    safe_diagnostics_path_under(&PathBuf::from(raw), &diagnostics_cache_root())
}

fn safe_diagnostics_path_under(path: &PathBuf, root: &PathBuf) -> Option<PathBuf> {
    let relative = path.strip_prefix(&root).ok()?;
    if relative.components().count() != 1 {
        return None;
    }
    let name = path.file_name()?.to_string_lossy();
    if !name.starts_with("v3-") || !name.ends_with(".json") {
        return None;
    }
    if path.exists() {
        let canonical_root = root.canonicalize().ok()?;
        let canonical_path = path.canonicalize().ok()?;
        if !canonical_path.starts_with(canonical_root) {
            return None;
        }
    }
    Some(path.clone())
}

fn privacy_text(value: &str) -> String {
    let normalized = value.replace('\\', "/");
    let text = normalized
        .split_whitespace()
        .map(|part| {
            let is_path_char = |character: char| character.is_ascii_alphanumeric() || character == '.' || character == '_';
            let start = part.find("/").filter(|index| {
                *index == 0 || !is_path_char(part[..*index].chars().last().unwrap_or(' '))
            });
            let drive = part.find(":/");
            let offset = drive.or(start);
            match offset {
                Some(index) => {
                    let suffix = &part[index..];
                    let basename = suffix.rsplit('/').next().unwrap_or_default();
                    format!("{}{basename}", &part[..index])
                }
                None => part.to_string(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    text.chars().take(512).collect()
}

fn summarize_worker_event(line: &str) -> String {
    let Ok(message) = serde_json::from_str::<Value>(line) else {
        return serde_json::json!({"type": "invalid", "raw": "unparseable"}).to_string();
    };
    let request_id = message.get("request_id").and_then(Value::as_str).unwrap_or("");
    let event_type = message.get("type").and_then(Value::as_str).unwrap_or("unknown");
    let mut summary = serde_json::Map::new();
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    summary.insert("timestamp_ms".to_string(), Value::from(timestamp_ms as u64));
    summary.insert("protocol".to_string(), message.get("protocol").cloned().unwrap_or(Value::Null));
    summary.insert("request_id".to_string(), Value::String(privacy_text(request_id)));
    summary.insert("type".to_string(), Value::String(event_type.to_string()));
    if let Some(payload) = message.get("payload").and_then(Value::as_object) {
        for key in ["state", "phase", "code", "message", "completed", "total"] {
            if let Some(value) = payload.get(key) {
                let safe = match value {
                    Value::String(text) => Value::String(privacy_text(text)),
                    _ => value.clone(),
                };
                summary.insert(key.to_string(), safe);
            }
        }
        if let Some(source) = payload.get("source").and_then(Value::as_str) {
            summary.insert("source".to_string(), Value::String(privacy_text(source)));
        }
        if let Some(result) = payload.get("result").and_then(Value::as_object) {
            for key in ["sources", "outputs", "failures", "diagnostics_sources", "duplicate_groups"] {
                if let Some(value) = result.get(key) {
                    let compact = if let Some(items) = value.as_array() {
                        Value::from(items.len() as u64)
                    } else {
                        value.clone()
                    };
                    summary.insert(key.to_string(), compact);
                }
            }
        }
    }
    let serialized = serde_json::Value::Object(summary).to_string();
    serialized.chars().take(MAX_WORKER_EVENT_LINE_BYTES).collect()
}

fn append_worker_event(line: &str) {
    let path = worker_events_path();
    append_bounded_record(&path, &summarize_worker_event(line), MAX_WORKER_EVENTS_BYTES);
}

fn append_bounded_record(path: &PathBuf, record: &str, max_bytes: u64) {
    let Some(parent) = path.parent() else { return };
    if std::fs::create_dir_all(parent).is_err() { return; }
    let record_size = record.len() as u64 + 1;
    if path.metadata().map(|metadata| metadata.len().saturating_add(record_size) > max_bytes).unwrap_or(false) {
        let rotated = path.with_extension("jsonl.1");
        let _ = std::fs::remove_file(&rotated);
        let _ = std::fs::rename(&path, rotated);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{record}");
    }
}

fn recent_worker_events() -> Vec<(String, Vec<u8>)> {
    [worker_events_path(), worker_events_path().with_extension("jsonl.1")]
        .into_iter()
        .filter_map(|path| {
            let name = path.file_name()?.to_string_lossy().into_owned();
            let bytes = std::fs::read(&path).ok()?;
            Some((name, bytes[bytes.len().saturating_sub(MAX_DIAGNOSTIC_LOG_BYTES)..].to_vec()))
        })
        .collect()
}

fn last_task_path() -> PathBuf {
    diagnostics_cache_root().join("last-task.json")
}

fn sanitize_diagnostic_value(value: &Value) -> Value {
    match value {
        Value::String(text) => Value::String(privacy_text(text)),
        Value::Array(items) => Value::Array(items.iter().map(sanitize_diagnostic_value).collect()),
        Value::Object(items) => Value::Object(
            items.iter().map(|(key, item)| (key.clone(), sanitize_diagnostic_value(item))).collect(),
        ),
        other => other.clone(),
    }
}

fn task_event_record(line: &str) -> Option<Value> {
    let message = serde_json::from_str::<Value>(line).ok()?;
    let event_type = message.get("type").and_then(Value::as_str).unwrap_or_default();
    if !matches!(event_type, "done" | "cancelled" | "error") { return None; }
    let request_id = message.get("request_id").and_then(Value::as_str).unwrap_or_default();
    if !(request_id.starts_with("main-") || request_id.starts_with("retry-")) { return None; }
    let payload = message.get("payload").cloned().unwrap_or(Value::Null);
    if payload.get("cleared").and_then(Value::as_bool) == Some(true) { return None; }
    if event_type == "done" && payload.get("result").and_then(Value::as_object).is_none() { return None; }
    let diagnostics_path = payload
        .get("result")
        .and_then(|result| result.get("diagnostics_path"))
        .and_then(Value::as_str)
        .and_then(safe_diagnostics_path);
    Some(serde_json::json!({
        "schema_version": 1,
        "saved_at": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64,
        "request_id": request_id,
        "type": event_type,
        "diagnostics_path": diagnostics_path.and_then(|path| path.file_name().map(|name| name.to_string_lossy().into_owned())),
        "payload": sanitize_diagnostic_value(&payload),
    }))
}

fn persist_task_event(line: &str) {
    let Some(record) = task_event_record(line) else { return; };
    let Ok(bytes) = serde_json::to_vec_pretty(&record) else { return; };
    if bytes.len() > MAX_LAST_TASK_BYTES { return; }
    let path = last_task_path();
    let Some(parent) = path.parent() else { return; };
    if std::fs::create_dir_all(parent).is_err() { return; }
    let temporary = path.with_extension("json.tmp");
    if std::fs::write(&temporary, bytes).is_ok() { let _ = std::fs::rename(temporary, path); }
}

fn recent_last_task() -> Option<Vec<u8>> {
    let bytes = std::fs::read(last_task_path()).ok()?;
    (bytes.len() <= MAX_LAST_TASK_BYTES).then_some(bytes)
}

fn persisted_diagnostics_path() -> Option<String> {
    let bytes = std::fs::read(last_task_path()).ok()?;
    let value: Value = serde_json::from_slice(&bytes).ok()?;
    let name = value.get("diagnostics_path")?.as_str()?;
    let path = diagnostics_cache_root().join(name);
    let safe = safe_diagnostics_path(&path.to_string_lossy())?;
    safe.is_file().then(|| safe.to_string_lossy().into_owned())
}

#[tauri::command]
fn export_diagnostics(default_name: String, entries: Vec<Value>, diagnostics_path: Option<String>) -> Result<Option<String>, String> {
    let mut archive_entries = Vec::with_capacity(entries.len() + 4);
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
    if let Some(entry) = recent_log_file(worker_log_path().with_extension("log.1"), "worker.log.1") {
        archive_entries.push(entry);
    }
    for (name, content) in recent_worker_events() {
        archive_entries.push((name, content));
    }
    if let Some(content) = recent_last_task() {
        archive_entries.push(("last-task.json".to_string(), content));
    }
    let diagnostics_path = persisted_diagnostics_path().or_else(|| {
        diagnostics_path.and_then(|raw| safe_diagnostics_path(&raw).map(|path| path.to_string_lossy().into_owned()))
    });
    if let Some(raw_path) = diagnostics_path {
        let path = safe_diagnostics_path(&raw_path);
        if path.is_none() {
            archive_entries.push((
                "per-source-diagnostics-unavailable.txt".to_string(),
                b"diagnostics unavailable: path outside PixelFlow cache\n".to_vec(),
            ));
        }
        let Some(path) = path else {
            let archive = build_zip_archive(archive_entries)?;
            let Some(path) = choose_save_path(&default_name, "导出诊断包", "ZIP files (*.zip)|*.zip")? else { return Ok(None); };
            std::fs::write(&path, archive).map_err(|error| format!("write diagnostics failed: {error}"))?;
            return Ok(Some(path.to_string_lossy().into_owned()));
        };
        match std::fs::metadata(&path) {
            Ok(metadata) if metadata.len() as usize <= MAX_DIAGNOSTIC_ENTRY_BYTES => match std::fs::read(&path) {
                Ok(content) => archive_entries.push(("per-source-diagnostics.json".to_string(), content)),
                Err(error) => archive_entries.push(("per-source-diagnostics-unavailable.txt".to_string(), format!("diagnostics unavailable: {error}\n").into_bytes())),
            },
            Ok(_) => archive_entries.push(("per-source-diagnostics-unavailable.txt".to_string(), b"diagnostics unavailable: file exceeds size limit\n".to_vec())),
            Err(error) => archive_entries.push((
                "per-source-diagnostics-unavailable.txt".to_string(),
                format!("diagnostics unavailable: {error}\n").into_bytes(),
            )),
        }
    } else {
        archive_entries.push((
            "per-source-diagnostics-unavailable.txt".to_string(),
            b"no per-source diagnostics artifact was recorded\n".to_vec(),
        ));
    }
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

#[cfg(any(target_os = "windows", test))]
fn windows_shell_path(path: &str) -> String {
    // Explorer opens the wrong folder when a Windows path carries forward
    // slashes, so normalize before handing the path over.
    path.replace('/', "\\")
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
    let status = Command::new("explorer")
        .arg(windows_shell_path(&path.to_string_lossy()))
        .status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = Command::new("xdg-open").arg(&path).status();
    let status = status.map_err(|error| format!("open directory failed: {error}"))?;
    // explorer.exe reports exit code 1 even when it opens the folder.
    #[cfg(target_os = "windows")]
    {
        let _ = status;
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    status
        .success()
        .then_some(())
        .ok_or_else(|| "open directory failed".to_string())
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

fn append_worker_log(message: &str) {
    use std::io::Write;
    if let Ok(mut file) = open_worker_log() {
        let _ = writeln!(file, "{message}");
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
            vec![OsString::from("-m"), OsString::from("pixelflow_core.worker")],
            Some(worker_dir()),
        ),
    };
    let mut command = Command::new(program);
    command.args(args);
    if let Some(directory) = working_dir {
        command.current_dir(directory);
    }
    hide_console(&mut command);
    // The protocol is UTF-8 in both directions. Without this the worker writes
    // its Chinese messages in the ANSI code page on Windows, and the host
    // cannot decode them.
    command.env("PYTHONIOENCODING", "utf-8").env("PYTHONUTF8", "1");
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
        let mut buffer = Vec::new();
        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) => break,
                Ok(_) => {}
                Err(error) => {
                    append_worker_log(&format!("worker stdout read failed: {error}"));
                    break;
                }
            }
            let text = match String::from_utf8(buffer.clone()) {
                Ok(text) => text,
                Err(error) => {
                    // A line the host cannot decode used to disappear without a
                    // trace, so the request that produced it never settled and
                    // the UI waited until its timeout. Keep the bytes and tell
                    // the UI instead of dropping them.
                    let bytes = error.as_bytes();
                    append_worker_log(&format!(
                        "worker stdout is not valid UTF-8 ({} bytes): {}",
                        bytes.len(),
                        String::from_utf8_lossy(bytes)
                    ));
                    let notice = serde_json::json!({
                        "protocol": 1,
                        "request_id": "worker",
                        "type": "error",
                        "payload": {
                            "code": "worker_encoding",
                            "message": "Worker 输出无法按 UTF-8 解码，已记录到 worker.log",
                        },
                    })
                    .to_string();
                    let _ = app.emit("worker-line", notice);
                    continue;
                }
            };
            let line = text.trim_end_matches(|character| character == '\n' || character == '\r');
            if line.is_empty() {
                continue;
            }
            append_worker_event(line);
            persist_task_event(line);
            if let Ok(message) = serde_json::from_str::<Value>(line) {
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
            let _ = app.emit("worker-line", line.to_string());
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
            emit_worker_exit(&app, "Worker 意外退出；已保留完成的输出，正在重启", task_id.clone(), true);
            restart_worker(&app, task_id);
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
            pick_directory,
            discover_models_directory,
            pick_file,
            authorize_logo_preview,
            save_text_file,
            export_diagnostics,
            open_path
        ])
        .build(tauri::generate_context!())
        .expect("error while building PixelFlow")
        .run(|app, event| {
            // Closing the main window has to end the app. The auxiliary windows
            // (settings / report / confirm) are hidden instead of destroyed so
            // they can be reused, and a hidden window still counts as open:
            // Tauri only exits once the window list is empty. Without this the
            // process survives with no window and no taskbar entry while it keeps
            // the worker alive, which on Windows leaves both executables locked
            // and makes the install directory impossible to delete.
            if let RunEvent::WindowEvent { label, event: tauri::WindowEvent::Destroyed, .. } = &event {
                if label.as_str() == "main" {
                    app.exit(0);
                }
            }
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

    #[test]
    fn saved_logo_preview_registers_only_its_parent_scope() {
        let path = std::env::temp_dir().join(format!("pixelflow-logo-{}.png", std::process::id()));
        let mut png_header = vec![0; 26];
        png_header[..8].copy_from_slice(b"\x89PNG\r\n\x1a\n");
        png_header[12..16].copy_from_slice(b"IHDR");
        png_header[16..20].copy_from_slice(&1440u32.to_be_bytes());
        png_header[20..24].copy_from_slice(&1440u32.to_be_bytes());
        png_header[25] = 6;
        std::fs::write(&path, png_header).unwrap();

        let mut supervisor = WorkerSupervisor {
            process: None,
            restart_history: VecDeque::new(),
            active_task_id: None,
            preview_roots: Vec::new(),
            shutting_down: false,
        };
        let path = path.to_string_lossy().into_owned();
        register_logo_preview(&mut supervisor, &path, "logo_square_dark").unwrap();

        let parent = PathBuf::from(&path).parent().unwrap().to_string_lossy().into_owned();
        let request = serde_json::json!({
            "payload": { "path": path, "allowed_roots": [parent] }
        });
        assert!(validate_preview_request(&supervisor, &request).is_ok());
        assert_eq!(supervisor.preview_roots.len(), 1);
        std::fs::remove_file(path).unwrap();
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
    fn windows_dialog_output_is_decoded_as_utf8() {
        assert!(windows_utf8_script("Write-Output test").contains("UTF8Encoding"));
        assert_eq!(decode_windows_stdout("文档\n".as_bytes()).unwrap(), "文档");
        assert!(decode_windows_stdout(&[0xff]).is_err());
    }

    #[test]
    fn windows_shell_path_normalizes_separators() {
        assert_eq!(windows_shell_path("C:/Users/a/主图"), "C:\\Users\\a\\主图");
        assert_eq!(windows_shell_path("C:\\Users\\a\\主图"), "C:\\Users\\a\\主图");
        assert_eq!(windows_shell_path("/tmp/out"), "\\tmp\\out");
    }

    #[test]
    fn diagnostic_archive_has_zip_records_and_rejects_traversal() {
        let archive = build_zip_archive(vec![("metadata.json".to_string(), b"{}".to_vec())]).unwrap();
        assert_eq!(&archive[..4], b"PK\x03\x04");
        assert!(archive.windows(4).any(|window| window == b"PK\x01\x02"));
        assert!(archive.windows(4).any(|window| window == b"PK\x05\x06"));
        assert!(validate_zip_entry_name("../outside.txt").is_err());
    }

    #[test]
    fn worker_event_summary_keeps_signal_and_redacts_paths() {
        let line = r#"{"protocol":1,"request_id":"job-1","type":"progress","payload":{"completed":2,"total":4,"phase":"生成","source":"/Users/test/input/red.jpg"}}"#;
        let summary: Value = serde_json::from_str(&summarize_worker_event(line)).unwrap();
        assert_eq!(summary["request_id"], "job-1");
        assert_eq!(summary["completed"], 2);
        assert_eq!(summary["source"], "red.jpg");
        assert!(summary["timestamp_ms"].as_u64().is_some());
        assert!(!summary.to_string().contains("/Users/"));
    }

    #[test]
    fn exported_logs_redact_home_paths() {
        let redacted = String::from_utf8(redact_log_bytes(b"Traceback: File \"/Users/private/input/photo.jpg\", line 4\n")).unwrap();
        assert!(redacted.contains("photo.jpg"));
        assert!(!redacted.contains("/Users/private"));
    }

    #[test]
    fn worker_event_rotation_keeps_current_and_previous_window() {
        let root = std::env::temp_dir().join(format!("pixelflow-event-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("worker-events.jsonl");
        let rotated = root.join("worker-events.jsonl.1");
        let record = summarize_worker_event(r#"{"protocol":1,"request_id":"job","type":"status","payload":{"state":"started"}}"#);
        let max_bytes = 160;
        std::fs::write(&path, vec![b'x'; (max_bytes as usize) - record.len()]).unwrap();
        append_bounded_record(&path, &record, max_bytes);
        assert!(rotated.is_file());
        assert!(path.is_file());
        assert_eq!(std::fs::metadata(&path).unwrap().len(), record.len() as u64 + 1);
        assert_eq!(std::fs::read_to_string(path).unwrap(), format!("{record}\n"));
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn diagnostics_path_is_limited_to_app_cache_and_filename_pattern() {
        let root = std::env::temp_dir().join(format!("pixelflow-diag-path-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join(format!("v3-test-{}.json", std::process::id()));
        std::fs::write(&path, b"{}").unwrap();
        assert_eq!(safe_diagnostics_path_under(&path, &root), Some(path.clone()));
        assert!(safe_diagnostics_path_under(&PathBuf::from("/tmp/unrelated.json"), &root).is_none());
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn task_event_record_keeps_safe_diagnostics_path_and_redacts_payload() {
        let line = r#"{"request_id":"main-7","type":"done","payload":{"result":{"diagnostics_path":"/Users/test/Library/Caches/PixelFlow/diagnostics/v3-test.json","outputs":["/Users/test/input/a.jpg"]}}}"#;
        let record = task_event_record(line).unwrap();
        assert_eq!(record["request_id"], "main-7");
        assert!(record["diagnostics_path"].is_null());
        assert_eq!(record["payload"]["result"]["outputs"][0], "a.jpg");
    }

    #[test]
    fn task_event_record_skips_classification_and_cache_clear_events() {
        let classify = r#"{"request_id":"classify-1","type":"done","payload":{"result":{"sources":1}}}"#;
        let clear = r#"{"request_id":"main-2","type":"done","payload":{"cleared":true}}"#;
        assert!(task_event_record(classify).is_none());
        assert!(task_event_record(clear).is_none());
    }
}
