; Inno Setup 6 script for the Windows installer.
; Built by `python build_exe.py --installer` (locally) or by CI, which pass the defines below.
; Installs per user by default (no admin rights needed): %LOCALAPPDATA%\Programs\Meeting Recorder

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\MeetingRecorder"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif
#ifndef OutputName
  #define OutputName "MeetingRecorder-setup"
#endif

[Setup]
; Keep AppId unchanged forever: it is how new versions find and upgrade old ones.
AppId={{CCB964B5-EB85-48D4-8E59-5584E423A3D1}
AppName=Meeting Recorder
AppVersion={#AppVersion}
AppVerName=Meeting Recorder {#AppVersion}
AppPublisher=Giacomo Falchetta
AppPublisherURL=https://github.com/giacfalk/meeting-transcribe-n-summarize
AppSupportURL=https://github.com/giacfalk/meeting-transcribe-n-summarize/issues
AppUpdatesURL=https://github.com/giacfalk/meeting-transcribe-n-summarize/releases
DefaultDirName={autopf}\Meeting Recorder
DefaultGroupName=Meeting Recorder
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\MeetingRecorder.exe
UninstallDisplayName=Meeting Recorder
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Upgrades: remove the previous version's libraries before copying the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"
Name: "{autodesktop}\Meeting Recorder"; Filename: "{app}\MeetingRecorder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MeetingRecorder.exe"; Description: "{cm:LaunchProgram,Meeting Recorder}"; Flags: nowait postinstall skipifsilent

; Settings (%APPDATA%\MeetingRecorder) and your recordings are left in place on uninstall.
