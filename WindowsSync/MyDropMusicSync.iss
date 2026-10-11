[Setup]
AppId={{FBD0E6A6-B90A-45B3-980D-2427843B5C9A}
AppName=MyDropMusic Sync
AppVersion=6.22.45
AppPublisher=WHYTHEN
DefaultDirName={localappdata}\Programs\MyDropMusic Sync
DefaultGroupName=MyDropMusic Sync
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=MyDropMusicSync_Setup_6_22_45
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\MyDropMusicSync.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Ajouter un raccourci sur le Bureau"; Flags: unchecked

[Files]
Source: "dist\MyDropMusicSync.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\MyDropMusic Sync"; Filename: "{app}\MyDropMusicSync.exe"
Name: "{userdesktop}\MyDropMusic Sync"; Filename: "{app}\MyDropMusicSync.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MyDropMusicSync.exe"; Description: "Ouvrir MyDropMusic Sync"; Flags: nowait postinstall skipifsilent
