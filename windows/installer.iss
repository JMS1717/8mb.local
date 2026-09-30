#define MyAppName "8mb.local"
#define MyAppVersion "143.0.0.0"
#define MyAppPublisher "JMS1717"
#define MyAppExeName "8mblocal.exe"
#ifndef MyAppArchitecture
  #define MyAppArchitecture "x64"
#endif
#ifndef MyAppOutputBaseFilename
  #define MyAppOutputBaseFilename "8mblocal-Setup"
#endif

[Setup]
AppId={{A11C4A7E-AC58-4A8A-9C45-8B10CA1A0001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\8mb.local
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename={#MyAppOutputBaseFilename}
Compression=lzma
SolidCompression=yes
ArchitecturesAllowed={#MyAppArchitecture}
ArchitecturesInstallIn64BitMode={#MyAppArchitecture}
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\build\brand\8mblocal.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Parameters: ""; Flags: nowait postinstall skipifsilent
