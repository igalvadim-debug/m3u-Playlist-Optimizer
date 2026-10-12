#!/usr/bin/env python3
"""
ClaudeAgent Coder - Alles-in-einem-Start (Python-Version)

Startet Filesystem-MCP-Proxy, RepoMapper-MCP, LanceDB-MCP und llama-server
als VIER TABS in EINEM Windows-Terminal-Fenster (statt vier Einzelfenster).
Modell 4 ist Qwen3.8-2B-BF16.gguf mit 32K Kontext.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\claudeAgent\Coder")
SERVER_EXE = Path(r"D:\Unsloth\C-claude\llama.cpp\llama-server.exe")
PROJECT_DIR = ROOT / "projects" / "MyPythonScripts"
REPOMAPPER_DIR = ROOT / "RepoMapper"
LANCEDB_DIR = ROOT / "lancedb_mcp"
VENV_PYTHON = REPOMAPPER_DIR / "venv" / "Scripts" / "python.exe"
HELPER_DIR = ROOT / "_start_helpers"
WT_EXE = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "wt.exe"

MODELS = {
    "1": ROOT / "models" / "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    "2": ROOT / "models" / "Qwopus3.5-4B-Coder-MTP-Q8_0.gguf",
    "3": ROOT / "models" / "gemma-4-E4B-it-heretic-Q4_K_M.gguf",
    "4": ROOT / "models" / "Qwen3.8-2B-BF16.gguf",
    "5": Path(r"D:\SillyTavernNeurogen\kobold\models\Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-Q6_K_P.gguf"),
}

# Vision-Modelle starten IMMER zusammen mit ihrem mmproj
MMPROJ_BY_MODEL = {
    MODELS["5"]: Path(r"D:\SillyTavernNeurogen\kobold\models\mmproj-Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-f16.gguf"),
}

PORTS = (8001, 8002, 8003, 8484)


def banner() -> None:
    print()
    print("==========================================================")
    print("ClaudeAgent Coder - Alles in einem Start (Python)")
    print("==========================================================")
    print()


def choose_model(arg_choice: str | None = None) -> Path:
    print("Verfuegbare Modelle:")
    for key, path in MODELS.items():
        print(f"[{key}] {path.name}")
    print()

    if arg_choice is not None:
        if arg_choice not in MODELS:
            print(f"[ERROR] Ungueltige Modell-Nummer als Argument: {arg_choice}")
            sys.exit(1)
        model_path = MODELS[arg_choice]
        print(f"Nicht-interaktiver Start - Modell {arg_choice} gewaehlt.")
    else:
        while True:
            choice = input(f"Modell waehlen (1-{len(MODELS)}): ").strip()
            if choice in MODELS:
                model_path = MODELS[choice]
                break
            print("Ungueltige Auswahl. Bitte erneut versuchen.")

    print()
    print("Gewaehltes Modell:")
    print(model_path)
    print()
    return model_path


def check_prerequisites(model_path: Path) -> None:
    checks = [
        (SERVER_EXE, "llama-server.exe nicht gefunden"),
        (model_path, "Modelldatei nicht gefunden"),
        *(((MMPROJ_BY_MODEL[model_path], "mmproj-Datei nicht gefunden"),) if model_path in MMPROJ_BY_MODEL else ()),
        (VENV_PYTHON, "RepoMapper Python nicht gefunden"),
        (PROJECT_DIR, "Projektverzeichnis nicht gefunden"),
        (WT_EXE, "Windows Terminal (wt.exe) nicht gefunden"),
    ]

    for path, message in checks:
        if not path.exists():
            print(f"[ERROR] {message}:")
            print(path)
            input("Enter zum Beenden druecken...")
            sys.exit(1)

    print("[OK] llama-server.exe")
    print("[OK] Modell")
    if model_path in MMPROJ_BY_MODEL:
        print("[OK] mmproj (Vision)")
    print("[OK] RepoMapper Python")
    print("[OK] Projektverzeichnis")
    print("[OK] Windows Terminal")
    print()


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def find_pids_on_port(port: int) -> list[int]:
    """Findet lauschende Prozesse per PowerShell, unabhängig von Windows-Sprache."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                f"-ErrorAction SilentlyContinue).OwningProcess",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        return [
            int(line.strip())
            for line in result.stdout.splitlines()
            if line.strip().isdigit()
        ]
    except Exception:
        return []


def kill_old_processes() -> None:
    print(f"Beende alte Prozesse auf den Ports {', '.join(map(str, PORTS))}...")
    for port in PORTS:
        for pid in find_pids_on_port(port):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
    print()


def wait_for_port(port: int, max_tries: int = 30) -> bool:
    for _ in range(max_tries):
        if port_is_open(port):
            return True
        time.sleep(1)
    return False


def write_helper_bat(command: str, cwd: Path, title: str, helper_name: str) -> Path:
    """Schreibt einen Befehl in eine BAT-Datei (für einen Tab)."""
    HELPER_DIR.mkdir(parents=True, exist_ok=True)
    helper_path = HELPER_DIR / f"run_{helper_name}.bat"

    content = (
        "@echo off\r\n"
        f"title {title}\r\n"
        f'cd /d "{cwd}"\r\n'
        f"{command}\r\n"
    )
    helper_path.write_text(content, encoding="utf-8")
    return helper_path


def open_wt_tabs(tabs: list[tuple[str, Path]]) -> None:
    """Oeffnet EIN Windows-Terminal-Fenster mit je einem Tab pro Eintrag."""
    args: list[str] = [str(WT_EXE)]
    for i, (title, bat_path) in enumerate(tabs):
        if i > 0:
            args.append(";")
        args += ["new-tab", "--title", title, "cmd.exe", "/k", str(bat_path)]
    subprocess.Popen(args)


def main() -> None:
    banner()
    arg_choice = sys.argv[1] if len(sys.argv) > 1 else None
    model_path = choose_model(arg_choice)
    check_prerequisites(model_path)
    kill_old_processes()

    fs_cmd = (
        'npx.cmd -y mcp-proxy@latest --port 8001 --stateless --server stream '
        f'--shell npx.cmd @modelcontextprotocol/server-filesystem "{PROJECT_DIR}"'
    )
    fs_bat = write_helper_bat(fs_cmd, cwd=ROOT, title="MCP Filesystem 8001", helper_name="fs")

    repomapper_cmd = f'"{VENV_PYTHON}" http_repomapper.py'
    repomapper_bat = write_helper_bat(
        repomapper_cmd, cwd=REPOMAPPER_DIR, title="MCP RepoMapper 8002", helper_name="repomapper"
    )

    lancedb_cmd = f'"{VENV_PYTHON}" server.py'
    lancedb_bat = write_helper_bat(
        lancedb_cmd, cwd=LANCEDB_DIR, title="MCP LanceDB 8003", helper_name="lancedb"
    )

    llama_cmd = " ".join(
        [
            f'"{SERVER_EXE}"',
            "-m", f'"{model_path}"',
            *(["--mmproj", f'"{MMPROJ_BY_MODEL[model_path]}"'] if model_path in MMPROJ_BY_MODEL else []),
            "-c", "65000",
            "-np", "1",
            "-ngl", "99",
            "-fa", "on",
            "--jinja",
            "--host", "127.0.0.1",
            "--port", "8484",
            "-ub", "512",
            "--temp", "0.2",
            "--top-p", "0.9",
            "--top-k", "40",
            "--ui-mcp-proxy",
        ]
    )
    llama_bat = write_helper_bat(
        llama_cmd, cwd=ROOT, title="llama-server 8484", helper_name="llama"
    )

    print("Oeffne EIN Windows-Terminal-Fenster mit 4 Tabs "
          "(Filesystem, RepoMapper, LanceDB, llama-server)...")
    open_wt_tabs(
        [
            ("MCP Filesystem 8001", fs_bat),
            ("MCP RepoMapper 8002", repomapper_bat),
            ("MCP LanceDB 8003", lancedb_bat),
            ("llama-server 8484", llama_bat),
        ]
    )
    print()

    print("Warte auf Filesystem MCP (8001), RepoMapper MCP (8002), LanceDB MCP (8003)...")
    for port, name in ((8001, "Filesystem MCP"), (8002, "RepoMapper MCP"), (8003, "LanceDB MCP")):
        if not wait_for_port(port):
            print(f"[WARN] {name} (Port {port}) noch nicht erreichbar - bitte den passenden Tab pruefen.")
        else:
            print(f"[OK] {name} auf Port {port}")
    print()

    print("Warte auf llama-server (8484) - Modell wird geladen, kann dauern...")
    if wait_for_port(8484, max_tries=120):
        print("[OK] llama-server auf Port 8484")
    else:
        print("[WARN] llama-server (Port 8484) noch nicht erreichbar - bitte den llama-server-Tab pruefen.")

    print()
    print("==========================================================")
    print("Web UI:")
    print("http://127.0.0.1:8484")
    print()
    print("MCP URLs:")
    print("Filesystem: http://127.0.0.1:8001/mcp")
    print("RepoMapper: http://127.0.0.1:8002/mcp")
    print("LanceDB   : http://127.0.0.1:8003/mcp")
    print()
    print("Alle 4 Dienste laufen als Tabs im selben Windows-Terminal-Fenster.")
    print("Dieses Startfenster kann jetzt geschlossen werden.")
    print("==========================================================")
    if arg_choice is None:
        input("Enter zum Beenden druecken...")


if __name__ == "__main__":
    main()
