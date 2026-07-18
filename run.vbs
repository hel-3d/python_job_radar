Option Explicit

Dim shell
Dim fso
Dim dir

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

dir = fso.GetParentFolderName(WScript.ScriptFullName)

shell.Run Chr(34) & fso.BuildPath(dir, "run_parser.bat") & Chr(34), 0, False
WScript.Sleep 2000

shell.Run Chr(34) & fso.BuildPath(dir, "run_ai_reviewer.bat") & Chr(34), 0, False
WScript.Sleep 2000

shell.Run Chr(34) & fso.BuildPath(dir, "run_upwork_parser.bat") & Chr(34), 0, False

Set fso = Nothing
Set shell = Nothing