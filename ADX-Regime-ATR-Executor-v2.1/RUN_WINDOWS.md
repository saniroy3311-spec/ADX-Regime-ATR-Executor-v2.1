# Windows launcher

`ADX_Regime_ATR_Executor.exe` is a Windows x64 launcher for the Python repository.

Requirements:

- Windows x64
- Python 3.11 or newer installed and available as `py`, `python`, or `python3`
- Internet access on first run so pip can install the packages in `requirements.txt`

Keep the EXE in the extracted repository root. Double-click it or run it from PowerShell.

The first run creates `.env` from the safe `.env.example`, creates `.venv`, installs dependencies, validates the configuration and then launches `main.py`.

This launcher does not embed Python. That keeps the full bot source transparent and editable. A fully frozen Python runtime should be built on a Windows runner if a no-Python installation is required.
