; Inno Setup script for FocusGuard — builds a compact, self-contained installer
; from the PyInstaller onedir output (dist\FocusGuard).
;
; Requires Inno Setup 6.3+ (uses the "x64compatible" architecture identifier, which
; replaced the legacy "x64" in 6.3; on 6.0–6.2 swap both Architectures* lines back to x64).
; Build:  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
;     or  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; Output: installer_output\FocusGuard-Setup.exe
;
; Per-user install (PrivilegesRequired=lowest) -> no UAC prompt, lands in
; %LOCALAPPDATA%\Programs\FocusGuard. The app itself only needs admin at runtime for
; the optional hosts-file site blocking; the installer does not.

#define MyAppName "FocusGuard"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "FocusGuard"
#define MyAppExeName "FocusGuard.exe"

[Setup]
AppId={{6F2B7E9A-1C4D-4B8E-9A3F-FOCUSGUARD0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
OutputBaseFilename=FocusGuard-Setup
SetupIconFile=app_icon.ico
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Strong compression that still fits the 32-bit Inno compiler's address space
; (ultra64 needs a multi-GB dictionary and runs out of memory here).
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Recurse the entire PyInstaller onedir build (FocusGuard.exe + _internal\).
Source: "dist\FocusGuard\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
