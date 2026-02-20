[Setup]
AppName=VideoSlim
AppVersion=2.0.0
AppPublisher=DongGuoZheng
AppPublisherURL=https://github.com/DongGuoZheng/VideoSlim
DefaultDirName={autopf}\VideoSlim
DefaultGroupName=VideoSlim
OutputDir=C:\Users\yooyu\Desktop
OutputBaseFilename=VideoSlim_Setup_v2.0.0
SetupIconFile=tools\icon.ico
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\VideoSlim.exe
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"

[Files]
Source: "dist\VideoSlim.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\config.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VideoSlim"; Filename: "{app}\VideoSlim.exe"
Name: "{group}\Uninstall VideoSlim"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VideoSlim"; Filename: "{app}\VideoSlim.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoSlim.exe"; Description: "Launch VideoSlim"; Flags: nowait postinstall skipifsilent
