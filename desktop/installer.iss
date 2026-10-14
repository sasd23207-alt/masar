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
; Python 3.8 uses Windows' AddDllDirectory API. On Windows 7, KB2533623 (or a
; superseding update such as KB3063858) is required. Embed the official MSU so
; the installer can install the prerequisite without a separate user download.
Source: "prerequisites\Windows6.1-KB3063858-x86.msu"; DestDir: "{tmp}"; Flags: dontcopy
Source: "prerequisites\Windows6.1-KB3063858-x64.msu"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\مِسار"; Filename: "{app}\Masar.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\مِسار"; Filename: "{app}\Masar.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Masar.exe"; Description: "تشغيل مِسار الآن"; Flags: nowait postinstall skipifsilent; Check: CanLaunchMasar

[Code]
var
  AccessCodePage: TInputQueryWizardPage;
  PrerequisiteRestartRequired: Boolean;

function IsWindows7: Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  Result := (Version.Major = 6) and (Version.Minor = 1);
end;

function CanLaunchMasar: Boolean;
begin
  Result := not PrerequisiteRestartRequired;
end;

function NeedRestart: Boolean;
begin
  Result := PrerequisiteRestartRequired;
end;

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

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UpdatePath: string;
  ResultCode: Integer;
  UpdateFileName: string;
begin
  Result := '';
  NeedsRestart := False;

  if not IsWindows7 then
    Exit;

  if IsWin64 then
    UpdateFileName := 'Windows6.1-KB3063858-x64.msu'
  else
    UpdateFileName := 'Windows6.1-KB3063858-x86.msu';

  try
    ExtractTemporaryFile(UpdateFileName);
  except
    Result := 'تعذر استخراج تحديث توافق Windows 7. أوقف التثبيت وأعد تنزيل ملف مِسار.';
    Exit;
  end;

  UpdatePath := ExpandConstant('{tmp}\' + UpdateFileName);
  if not Exec(ExpandConstant('{sys}\wusa.exe'),
      '"' + UpdatePath + '" /quiet /norestart', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
  begin
    Result := 'تعذر تشغيل مثبت تحديث توافق Windows 7. تأكد من تشغيل المثبّت بصفة المسؤول.';
    Exit;
  end;

  if ResultCode = 3010 then
  begin
    PrerequisiteRestartRequired := True;
    MsgBox(
      'تم تثبيت تحديث توافق Windows 7 المطلوب لتشغيل مِسار. احفظ أعمالك وأعد تشغيل الكمبيوتر عند انتهاء التثبيت. لا تفتح مِسار قبل إعادة التشغيل.',
      mbInformation, MB_OK);
  end
  else if (ResultCode = 0) or (ResultCode = 2359302) or
          (ResultCode = $80240017) then
  begin
    { Installed, already installed, or superseded/not applicable on this patched OS. }
  end
  else
  begin
    Result := 'تعذر تثبيت تحديث توافق Windows 7 (رمز ' +
      IntToStr(ResultCode) + '). تأكد من أن النظام Windows 7 SP1 ومحدّث، ثم أعد المحاولة.';
    Exit;
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
