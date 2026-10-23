"""Small desktop launcher for running individual unittest packages."""

from __future__ import annotations

import queue
import subprocess
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_ROOT = PROJECT_ROOT / "tests"
LOG_DIRECTORY = TEST_ROOT / "testing_logs"


class TestRunnerApp:
    """Run a test package or the full suite and show live console output."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AI Adventure Test Runner")
        self.root.geometry("1000x700")
        self.root.minsize(720, 480)
        LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)

        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.output_lines: list[str] = []
        self.active_process: subprocess.Popen[str] | None = None
        self.can_save_output = False
        self.run_buttons: list[ttk.Button] = []

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            outer,
            text="Run tests by package",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            outer,
            text="All Tests isolates packages and aggregates results. Passing-test output is buffered; failures show details.",
        ).pack(anchor=tk.W, pady=(3, 10))

        button_area = ttk.Frame(outer)
        button_area.pack(fill=tk.X, pady=(0, 10))
        packages = sorted(
            folder.name
            for folder in TEST_ROOT.iterdir()
            if folder.is_dir()
            and folder.name.endswith("_Tests")
            and (PROJECT_ROOT / f"{folder.name}.bat").is_file()
        )
        targets = []
        if (PROJECT_ROOT / "All_Tests.bat").is_file():
            targets.append(("All Tests", "All_Tests"))
        targets.extend((package.replace("_", " "), package) for package in packages)
        for index, (label, package) in enumerate(targets):
            button = ttk.Button(
                button_area,
                text=label,
                command=lambda name=package: self.start_package(name),
            )
            button.grid(row=index // 4, column=index % 4, sticky="ew", padx=4, pady=4)
            self.run_buttons.append(button)
        for column in range(4):
            button_area.columnconfigure(column, weight=1)

        log_header = ttk.Frame(outer)
        log_header.pack(fill=tk.X)
        ttk.Label(log_header, text="Console log", font=("Segoe UI", 11, "bold")).pack(
            side=tk.LEFT
        )
        self.save_button = ttk.Button(
            log_header, text="Save output to .log…", command=self.save_output, state=tk.DISABLED
        )
        self.save_button.pack(side=tk.RIGHT)
        ttk.Button(log_header, text="Clear", command=self.clear_output).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        self.console = ScrolledText(
            outer,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
            background="#111418",
            foreground="#e7e9ec",
            insertbackground="#e7e9ec",
        )
        self.console.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.root.after(75, self.drain_output_queue)

    def append_output(self, line: str) -> None:
        self.output_lines.append(line)
        self.console.configure(state=tk.NORMAL)
        self.console.insert(tk.END, line)
        self.console.see(tk.END)
        self.console.configure(state=tk.DISABLED)

    def start_package(self, package: str) -> None:
        if self.active_process is not None:
            return

        script = PROJECT_ROOT / f"{package}.bat"
        if not script.is_file():
            messagebox.showerror("Batch script not found", f"Could not find:\n{script}")
            return

        self.output_lines.clear()
        self.can_save_output = False
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)
        self.save_button.configure(state=tk.DISABLED)
        self.append_output(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] Starting {package}\n")
        for button in self.run_buttons:
            button.configure(state=tk.DISABLED)

        thread = threading.Thread(target=self.run_batch_script, args=(package, script), daemon=True)
        thread.start()

    def run_batch_script(self, package: str, script: Path) -> None:
        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", "call", str(script)],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self.active_process = process
            assert process.stdout is not None
            for line in process.stdout:
                self.output_queue.put(("line", line))
            return_code = process.wait()
            self.output_queue.put(("finished", f"{package}\t{return_code}"))
        except Exception as error:
            self.output_queue.put(("error", f"Could not run {package}: {type(error).__name__}: {error}\n"))

    def drain_output_queue(self) -> None:
        try:
            while True:
                kind, value = self.output_queue.get_nowait()
                if kind == "line":
                    self.append_output(value)
                elif kind == "error":
                    self.append_output(value)
                    self.finish_run()
                elif kind == "finished":
                    package, exit_code = value.split("\t", maxsplit=1)
                    status = "passed" if exit_code == "0" else "finished with failures"
                    self.append_output(
                        f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
                        f"{package} {status} (exit code {exit_code}).\n"
                    )
                    self.active_process = None
                    self.finish_run()
        except queue.Empty:
            pass
        self.root.after(75, self.drain_output_queue)

    def finish_run(self) -> None:
        self.active_process = None
        self.can_save_output = bool(self.output_lines)
        for button in self.run_buttons:
            button.configure(state=tk.NORMAL)
        self.save_button.configure(state=tk.NORMAL if self.can_save_output else tk.DISABLED)

    def clear_output(self) -> None:
        if self.active_process is not None:
            return
        self.output_lines.clear()
        self.can_save_output = False
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)
        self.save_button.configure(state=tk.DISABLED)

    def save_output(self) -> None:
        if not self.can_save_output or not self.output_lines:
            return
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        package = "test_run"
        for line in self.output_lines:
            if "Starting " in line:
                package = line.rsplit("Starting ", maxsplit=1)[-1].strip()
                break
        destination = LOG_DIRECTORY / f"{package}_{timestamp}.log"
        suffix = 1
        while destination.exists():
            destination = LOG_DIRECTORY / f"{package}_{timestamp}_{suffix}.log"
            suffix += 1
        try:
            Path(destination).write_text("".join(self.output_lines), encoding="utf-8")
        except OSError as error:
            messagebox.showerror("Could not save log", f"{type(error).__name__}: {error}")
            return
        messagebox.showinfo("Log saved", f"Test output saved to:\n{destination}")


def main() -> None:
    root = tk.Tk()
    TestRunnerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
