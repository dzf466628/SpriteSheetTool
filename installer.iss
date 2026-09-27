; SpriteSheetTool 传统 Windows 安装向导（与 HTBCC 同样使用 Inno Setup 6）
#define MyAppName "SpriteSheetTool"
#define MyAppVersion "1.1.44"
#define MyAppPublisher "SpriteSheetTool"
#define MyAppExeName "SpriteSheetTool.exe"

[Setup]
AppId={{D9B3B795-3F54-4A38-BB43-6C0C39B6F8C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=SpriteSheetTool_v.{#MyAppVersion}
SetupIconFile=SpriteSheetTool.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
MinVersion=10.0
; 自动更新场景：安装时自动关闭正在运行的旧版本，安装完成后由 [Run] 拉起新版本
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
; PyInstaller onedir：将整个运行时目录递归安装，避免单文件临时解压。
Source: "dist\SpriteSheetTool\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; 快捷方式使用的图标放在安装根目录，避免 PyInstaller 6 的 _internal 目录布局差异。
Source: "SpriteSheetTool.ico"; DestDir: "{app}"; Flags: ignoreversion
; .sstool 工程文件使用独立图标，软件自身仍使用 SpriteSheetTool.ico。
Source: "SSTool.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 更新版本时移除旧版本号快捷方式，避免桌面重复。
Type: files; Name: "{autodesktop}\SpriteSheetTool v*"
Type: files; Name: "{autoprograms}\SpriteSheetTool v*"

[Icons]
Name: "{autodesktop}\SpriteSheetTool v{#MyAppVersion}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\SpriteSheetTool.ico"; WorkingDir: "{app}"
Name: "{autoprograms}\SpriteSheetTool v{#MyAppVersion}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\SpriteSheetTool.ico"; WorkingDir: "{app}"

[Registry]
; 在当前用户范围内注册 .sstool，避免安装程序要求管理员权限。
Root: HKCU; Subkey: "Software\Classes\.sstool"; ValueType: string; ValueName: ""; ValueData: "SpriteSheetTool.Project"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\SpriteSheetTool.Project"; ValueType: string; ValueName: ""; ValueData: "SpriteSheetTool 工程"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\SpriteSheetTool.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\SSTool.ico"
; 注意：Inno Setup 不支持 \ 转义双引号，字符串里的引号要写成双双写（""）。
Root: HKCU; Subkey: "Software\Classes\SpriteSheetTool.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
; ---- 资源管理器缩略图 ----
; .sstool 文件以一张合法 PNG 开头（工程里存的那张带角标的画面），把本扩展名挂到
; 系统自带的图片缩略图处理器上，资源管理器就能直接显示工程内容缩略图。
; 注：Inno Setup 里字面左大括号要写成 {{（右大括号不用转义，写一个即可）。
Root: HKCU; Subkey: "Software\Classes\.sstool\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}"; ValueType: string; ValueName: ""; ValueData: "{{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}"; Flags: uninsdeletekey
; 声明为图片类型，让"按类型分组/排序"和预览窗格也按图片对待。
Root: HKCU; Subkey: "Software\Classes\.sstool"; ValueType: string; ValueName: "PerceivedType"; ValueData: "image"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.sstool"; ValueType: string; ValueName: "Content Type"; ValueData: "image/png"; Flags: uninsdeletevalue

[Run]
; 安装完成后启动软件。不加 skipifsilent：自动更新走静默安装时也能自动重启新版本。
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall runasoriginaluser
