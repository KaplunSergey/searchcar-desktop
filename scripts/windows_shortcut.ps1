# WScript.Shell.TargetPath loses characters outside the Windows ANSI code page.
# Share the Unicode reader between the installer smoke and customer diagnostic.
if (-not ('SearchCarShortcut' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class SearchCarShortcut {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink {}
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                     int capacity, IntPtr findData, uint flags);
    }
    public static string Target(string file) {
        object link = new ShellLink();
        try {
            ((IPersistFile)link).Load(file, 0);
            var path = new StringBuilder(32768);
            ((IShellLinkW)link).GetPath(path, path.Capacity, IntPtr.Zero, 0);
            return path.ToString();
        } finally { Marshal.ReleaseComObject(link); }
    }
}
'@
}

function Get-SearchCarShortcutTarget([string]$Path) {
    [SearchCarShortcut]::Target($Path)
}
