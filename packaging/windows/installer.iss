; Inno Setup script for SubtitleTranslator.
; Version is passed from CI:  ISCC /DMyAppVersion=1.2.3 installer.iss
; The PyInstaller --onedir output is expected in ..\..\dist\SubtitleTranslator

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

; Directory that dist\, icon.ico and installer_output\ are resolved against.
; Defaults to two levels up (repo root) when built locally from packaging\windows.
#ifndef RepoRoot
  #define RepoRoot SourcePath + "..\..\"
#endif

#define MyAppName "SubtitleTranslator"
#define MyAppExeName "SubtitleTranslator.exe"
#define MyAppPublisher "Dmitrii Maksimov"
#define MyAppURL "https://github.com/dmitrii-maksimov/subtitle-translator"

[Setup]
AppId={{B9F3D6E2-1A7C-4E5B-9C21-9A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
SourceDir={#RepoRoot}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
OutputBaseFilename=SubtitleTranslator-Setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\SubtitleTranslator\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
