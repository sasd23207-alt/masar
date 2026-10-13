#define MyAppName "مِسار"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{2BFF8F6A-9B59-45D2-96A7-F113A4991BD7}
AppName={#MyAppName} - مدرسة الريادة
AppVersion={#MyAppVersion}
AppPublisher=مِسار
DefaultDirName={autopf}\Masar
DefaultGroupName=مِسار
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\Masar.exe
OutputDir=release
OutputBaseFilename=Masar-Setup-Windows7-32bit
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
MinVersion=6.1sp1

[Dirs]
; These folders deliberately survive uninstall and program upgrades.
Name: "{commonappdata}\Masar"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Masar\data"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Masar\backups"; Permissions: users-modify; Flags: uninsneveruninstall
Name: "{commonappdata}\Masar\logs"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "release\win-unpacked\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\مِسار"; Filename: "{app}\Masar.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\مِسار"; Filename: "{app}\Masar.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Masar.exe"; Description: "تشغيل مِسار الآن"; Flags: nowait postinstall skipifsilent

[Code]
var
  AccessCodePage: TInputQueryWizardPage;

procedure InitializeWizard;
var
  ConfigFile: string;
begin
  ConfigFile := ExpandConstant('{commonappdata}\Masar\.env');
  if not FileExists(ConfigFile) then
  begin
    AccessCodePage := CreateInputQueryPage(
      wpSelectDir,
      'إعداد مِسار',
      'رمز تفعيل المدرسة',
      'أدخل رمز التفعيل المخصص للمدرسة. يُحفظ الرمز على هذا الكمبيوتر ولا يُضمّن داخل ملف التثبيت.'
    );
    AccessCodePage.Add('رمز التفعيل:', True);
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if Assigned(AccessCodePage) and (CurPageID = AccessCodePage.ID) and
     (Trim(AccessCodePage.Values[0]) = '') then
  begin
    MsgBox('أدخل رمز التفعيل للمتابعة.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: string;
  AccessCode: string;
begin
  if CurStep = ssPostInstall then
  begin
    ConfigFile := ExpandConstant('{commonappdata}\Masar\.env');
    if (not FileExists(ConfigFile)) and Assigned(AccessCodePage) then
    begin
      AccessCode := Trim(AccessCodePage.Values[0]);
      StringChangeEx(AccessCode, #13, '', True);
      StringChangeEx(AccessCode, #10, '', True);
      if not SaveStringToFile(ConfigFile, 'MASAR_ACCESS_CODE=' + AccessCode + #13#10, False) then
        MsgBox('تعذر حفظ إعداد التفعيل. أعد تشغيل المثبّت بصفة المسؤول أو تواصل مع مسؤول النظام.', mbError, MB_OK);
    end;
  end;
end;
