"""私有数据权限：POSIX 模式位与 Windows 当前用户 DACL。"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def restrict_access(path: Path, *, directory: bool = False) -> None:
    """失败时抛出异常；调用者不能继续向未受保护的文件写入内容。"""
    if path.is_symlink():
        raise OSError(f"私有数据路径不能是符号链接：{path}")
    if os.name == "nt":
        _restrict_windows(path, directory=directory)
    else:
        mode = 0o700 if directory else 0o600
        path.chmod(mode)
        if stat.S_IMODE(path.stat().st_mode) != mode:
            raise PermissionError(f"文件系统未应用私有权限，请使用支持 POSIX 模式的数据目录：{path}")


def private_directory(path: Path) -> None:
    # 只收紧应用数据目录，不改变配置目录及其祖先的权限。
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    restrict_access(path, directory=True)


def _restrict_windows(path: Path, *, directory: bool) -> None:
    """用进程用户 SID 替换 DACL，移除宽泛的继承和显式授权。

    目录 ACE 可继承，SQLite 新建的 WAL/SHM 也受到保护。
    标准库 ctypes 调用系统 API，不依赖外部 CLI 或新增包。
    """
    import ctypes
    from ctypes import wintypes as w

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, pointer, w.DWORD, ctypes.POINTER(w.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(w.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(pointer), pointer]
    advapi.GetSecurityDescriptorDacl.argtypes = [pointer, ctypes.POINTER(w.BOOL), ctypes.POINTER(pointer), ctypes.POINTER(w.BOOL)]
    advapi.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, pointer, pointer, pointer, pointer]
    advapi.SetNamedSecurityInfoW.restype = w.DWORD

    token = w.HANDLE()
    sid_text = w.LPWSTR()
    descriptor = pointer()

    def checked(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    try:
        checked(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))  # TOKEN_QUERY
        size = w.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(size.value)
        checked(advapi.GetTokenInformation(token, 1, user, size, ctypes.byref(size)))
        # TOKEN_USER 以 SID_AND_ATTRIBUTES 开头，其首字段是 SID 指针。
        sid = ctypes.cast(user, ctypes.POINTER(pointer))[0]
        checked(advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)))
        inheritance = "OICI" if directory else ""
        sddl = f"D:P(A;{inheritance};FA;;;{sid_text.value})"
        checked(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
        present, defaulted, dacl = w.BOOL(), w.BOOL(), pointer()
        checked(advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
        # SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        error = advapi.SetNamedSecurityInfoW(str(path.resolve()), 1, 0x80000004, None, None, dacl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(sid_text)
        if token:
            kernel.CloseHandle(token)
