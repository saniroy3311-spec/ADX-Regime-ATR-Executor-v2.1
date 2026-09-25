module.exports = {
  apps: [
    {
      name: "adx-regime-atr-executor",
      cwd: "/opt/adx-regime-atr-executor",
      script: "main.py",
      interpreter: "/opt/adx-regime-atr-executor/.venv/bin/python",
      env_file: "/opt/adx-regime-atr-executor/.env",
      autorestart: true,
      restart_delay: 5000
    }
  ]
};
