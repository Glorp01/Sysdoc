#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

[Setup]
AppId={{AB1782E0-C06E-46D7-921F-2DD417FBCA64}
AppName=Sysdoc
AppVersion={#AppVersion}
AppPublisher=Glorp01
AppPublisherURL=https://github.com/Glorp01/Sysdoc
AppSupportURL=https://github.com/Glorp01/Sysdoc/issues
AppUpdatesURL=https://github.com/Glorp01/Sysdoc/releases/latest
DefaultDirName={localappdata}\Programs\Sysdoc
DefaultGroupName=Sysdoc
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\release
OutputBaseFilename=Sysdoc-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\sysdoc-gui.exe
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\windows\sysdoc-gui.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\windows\sysdoc.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Sysdoc"; Filename: "{app}\sysdoc-gui.exe"
Name: "{autoprograms}\Sysdoc AI Assistant"; Filename: "{app}\sysdoc.exe"; Comment: "Diagnose and fix PC problems with AI"
Name: "{autodesktop}\Sysdoc"; Filename: "{app}\sysdoc-gui.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\sysdoc-gui.exe"; Description: "Open Sysdoc"; Flags: nowait postinstall skipifsilent
Filename: "{app}\sysdoc-gui.exe"; Flags: nowait; Check: RestartAfterUpdate

[Code]
function RestartAfterUpdate: Boolean;
var
  I: Integer;
begin
  Result := False;
  if not WizardSilent then Exit;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RESTARTAPP') = 0 then
      Result := True;
end;
