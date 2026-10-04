use std::{
    collections::{HashSet, VecDeque},
    ffi::OsString,
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
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
    let mut command = Command::new("powershell");
    command.args(["-NoProfile", "-STA", "-Command", script]);
    hide_console(&mut command);
    let output = command
        .output()
        .map_err(|error| format!("file dialog failed: {error}"))?;
    if !output.status.success() {
        return Err(format!("file dialog failed: {}", String::from_utf8_lossy(&output.stderr)));
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((!path.is_empty()).then_some(path))
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
        let mut command = Command::new("powershell");
        command
            .args(["-NoProfile", "-STA", "-Command", script])
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
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
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
                executable.is_file().then_some(executable)
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
        for line in BufReader::new(stdout).lines().flatten() {
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
    fn diagnostic_archive_has_zip_records_and_rejects_traversal() {
        let archive = build_zip_archive(vec![("metadata.json".to_string(), b"{}".to_vec())]).unwrap();
        assert_eq!(&archive[..4], b"PK\x03\x04");
        assert!(archive.windows(4).any(|window| window == b"PK\x01\x02"));
        assert!(archive.windows(4).any(|window| window == b"PK\x05\x06"));
        assert!(validate_zip_entry_name("../outside.txt").is_err());
    }
}
