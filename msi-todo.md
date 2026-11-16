# Windows MSI Wiring TODO

## WSL 2 Distro Installer Integration

Branch `native/win-3b` was not yet merged into main at the time this WSL 2 distro work was implemented. Do not copy that branch's legacy native supervisor/service code.

When the WiX MSI installer (`packaging/windows/KeloServer.wxs`) is authored or updated for the WSL 2 distro architecture, its deferred custom actions must call the two PowerShell scripts in `scripts/windows/`:

1. **Install Action (Deferred Elevated Custom Action)**:
   - Command:
     ```powershell
     powershell.exe -NoProfile -ExecutionPolicy Bypass -File "[#kelo_wsl_install.ps1]" -DistroTar "[#kelo_wsl_amd64.tar.gz]"
     ```
   - Must run with elevated privileges (deferred execution in system context, or as installing user via custom action credentials).
   - Handles WSL 2 prerequisites, WSL installation, reboot notification (`exit 3010`), distro import, offline Kelo install, and Scheduled Task registration.

2. **Uninstall Action (Deferred Elevated Custom Action)**:
   - Command:
     ```powershell
     powershell.exe -NoProfile -ExecutionPolicy Bypass -File "[#kelo_wsl_uninstall.ps1]"
     ```
   - Supports keeping data (defaults to exporting backup tarball to `%ProgramData%\Kelo\backup-<date>.tar`) unless explicitly commanded to delete data.

3. **Files Bundled in the MSI**:
   - `scripts/windows/kelo-wsl-install.ps1`
   - `scripts/windows/kelo-wsl-uninstall.ps1`
   - `dist/kelo-wsl-<version>-amd64.tar.gz` (the Kelo WSL 2 rootfs built by `tools/build/wsl-distro.sh`)
