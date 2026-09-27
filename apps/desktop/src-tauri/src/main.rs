use std::{
    ffi::OsString,
    collections::VecDeque,
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

const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);
const RESTART_BACKOFF: [Duration; MAX_RESTARTS] = [
    Duration::from_millis(250),
    Duration::from_secs(1),
    Duration::from_secs(2),
];

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
fn pick_directory(state: State<'_, WorkerState>) -> Result<Option<String>, String> {
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
        let output = Command::new("osascript")
            .args(["-e", "POSIX path of (choose folder with prompt \"选择文件夹\")"])
            .output()
            .map_err(|error| format!("directory picker failed: {error}"))?;
        if output.status.success() {
            return Ok(remember(Some(String::from_utf8_lossy(&output.stdout).trim().to_string())));
        }
        return Ok(None);
    }
    #[cfg(target_os = "windows")]
    {
        return Ok(remember(windows_dialog("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")?));
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    { Ok(None) }
}

#[tauri::command]
fn pick_file(key: String, state: State<'_, WorkerState>) -> Result<Option<String>, String> {
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
        let output = Command::new("osascript")
            .args(["-e", "POSIX path of (choose file with prompt \"选择 PNG Logo\" of type {\"public.png\"})"])
            .output()
            .map_err(|error| format!("file picker failed: {error}"))?;
        if output.status.success() {
            let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
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
fn save_text_file(default_name: String, content: String) -> Result<Option<String>, String> {
    #[cfg(target_os = "macos")]
    {
        let safe_name = default_name.replace('"', "'");
        let script = format!(
            "POSIX path of (choose file name with prompt \"导出失败报告\" default name \"{safe_name}\")"
        );
        let output = Command::new("osascript")
            .args(["-e", &script])
            .output()
            .map_err(|error| format!("save dialog failed: {error}"))?;
        if !output.status.success() {
            return Ok(None);
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        std::fs::write(&path, content).map_err(|error| format!("write report failed: {error}"))?;
        return Ok(Some(path));
    }
    #[cfg(target_os = "windows")]
    {
        let script = "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.SaveFileDialog; $d.FileName = $env:PIXELFLOW_REPORT_NAME; $d.Filter = 'Text files (*.txt)|*.txt'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }";
        let mut command = Command::new("powershell");
        command
            .args(["-NoProfile", "-STA", "-Command", script])
            .env("PIXELFLOW_REPORT_NAME", &default_name);
        hide_console(&mut command);
        let output = command
            .output()
            .map_err(|error| format!("save dialog failed: {error}"))?;
        if !output.status.success() {
            return Err(format!("save dialog failed: {}", String::from_utf8_lossy(&output.stderr)));
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() { return Ok(None); }
        std::fs::write(&path, content).map_err(|error| format!("write report failed: {error}"))?;
        return Ok(Some(path));
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    { Ok(None) }
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

fn reserve_restart(app: &AppHandle) -> Option<Duration> {
    let state = app.state::<WorkerState>();
    let mut guard = state.0.lock().ok()?;
    if guard.shutting_down {
        return None;
    }
    let now = Instant::now();
    guard
        .restart_history
        .retain(|started| now.duration_since(*started) < RESTART_WINDOW);
    if guard.restart_history.len() >= MAX_RESTARTS {
        return None;
    }
    let attempt = guard.restart_history.len();
    guard.restart_history.push_back(now);
    Some(RESTART_BACKOFF[attempt])
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
        .invoke_handler(tauri::generate_handler![worker_send, pick_directory, pick_file, save_text_file, open_path])
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
