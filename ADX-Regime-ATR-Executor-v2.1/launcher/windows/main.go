package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type pythonCommand struct {
	exe  string
	args []string
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, in); err != nil {
		return err
	}
	return out.Sync()
}

func runInteractive(exe string, args ...string) error {
	cmd := exec.Command(exe, args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func findPython() *pythonCommand {
	candidates := []pythonCommand{
		{exe: "py", args: []string{"-3"}},
		{exe: "python", args: nil},
		{exe: "python3", args: nil},
	}
	for _, c := range candidates {
		// Require Python 3.11+ because the packaged runtime and dependencies are
		// validated against modern Python versions. Do not silently accept an
		// older interpreter just because it answers --version.
		check := append(append([]string{}, c.args...), "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
		cmd := exec.Command(c.exe, check...)
		if err := cmd.Run(); err == nil {
			x := c
			return &x
		}
	}
	return nil
}

func requirementHash(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:]), nil
}

func pause() {
	fmt.Print("\nPress Enter to close...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func fail(msg string, err error) {
	fmt.Fprintln(os.Stderr, "\nERROR:", msg)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	pause()
	os.Exit(1)
}

func main() {
	exePath, err := os.Executable()
	if err != nil {
		fail("Could not locate the launcher executable.", err)
	}
	root := filepath.Dir(exePath)
	if err := os.Chdir(root); err != nil {
		fail("Could not switch to the bot folder.", err)
	}

	fmt.Println("============================================================")
	fmt.Println("ADX Regime ATR Executor v2.1 - Windows Launcher")
	fmt.Println("============================================================")
	fmt.Println("Project folder:", root)
	fmt.Println("Default package mode is PAPER + TESTNET unless you edit .env.")
	fmt.Println()

	if !exists("main.py") || !exists("requirements.txt") || !exists(".env.example") {
		fail("Keep this EXE in the root of the extracted optimized repository.", nil)
	}

	if !exists(".env") {
		if err := copyFile(".env.example", ".env"); err != nil {
			fail("Could not create .env from .env.example.", err)
		}
		fmt.Println("Created .env from the safe template.")
	}

	venvPython := filepath.Join(root, ".venv", "Scripts", "python.exe")
	if !exists(venvPython) {
		py := findPython()
		if py == nil {
			fail("Python 3.11+ was not found. Install Python, then run this EXE again.", nil)
		}
		fmt.Println("Creating Python virtual environment...")
		args := append(append([]string{}, py.args...), "-m", "venv", ".venv")
		if err := runInteractive(py.exe, args...); err != nil {
			fail("Could not create .venv.", err)
		}
	}

	if !exists(venvPython) {
		fail("Virtual environment was created but python.exe was not found.", nil)
	}

	reqHash, err := requirementHash("requirements.txt")
	if err != nil {
		fail("Could not hash requirements.txt.", err)
	}
	sentinel := filepath.Join(root, ".venv", ".adx_requirements.sha256")
	previous := ""
	if b, err := os.ReadFile(sentinel); err == nil {
		previous = strings.TrimSpace(string(b))
	}
	if previous != reqHash {
		fmt.Println("Installing/updating Python dependencies. This may take a few minutes...")
		if err := runInteractive(venvPython, "-m", "pip", "install", "-r", "requirements.txt"); err != nil {
			fail("Dependency installation failed.", err)
		}
		if err := os.WriteFile(sentinel, []byte(reqHash+"\n"), 0644); err != nil {
			fail("Could not write dependency state file.", err)
		}
	}

	fmt.Println()
	fmt.Println("Validating configuration...")
	if err := runInteractive(venvPython, "validate_setup.py"); err != nil {
		fail("Configuration validation failed. Fix .env and run again.", err)
	}

	fmt.Println()
	fmt.Println("Starting ADX Regime ATR Executor...")
	fmt.Println("Close this window or use Ctrl+C to stop the local process.")
	fmt.Println()
	if err := runInteractive(venvPython, "main.py"); err != nil {
		fail("The trading process stopped with an error.", err)
	}
}
