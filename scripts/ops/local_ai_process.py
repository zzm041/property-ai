"""Windows request-owned process containment, without process-name cleanup.

The actual interpreter joins before loading models; this also covers the
Windows venv launcher's second interpreter. No dependencies or global settings.
"""
import ctypes
from ctypes import wintypes as w
import os
import time
from uuid import uuid4


class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),
              ('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),
              ('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',ctypes.c_uint64*6),
              ('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
              ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_=[('times',ctypes.c_int64*4),('faults',w.DWORD),
              ('total',w.DWORD),('active',w.DWORD),('terminated',w.DWORD)]


def kernel():
    if os.name!='nt':raise RuntimeError('WINDOWS_REQUEST_JOB_REQUIRED')
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    specs={
        'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],w.HANDLE),
        'OpenJobObjectW':([w.DWORD,w.BOOL,w.LPCWSTR],w.HANDLE),
        'SetInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
        'QueryInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p],w.BOOL),
        'AssignProcessToJobObject':([w.HANDLE,w.HANDLE],w.BOOL),
        'TerminateJobObject':([w.HANDLE,w.UINT],w.BOOL),
        'GetCurrentProcess':([],w.HANDLE),
        'CloseHandle':([w.HANDLE],w.BOOL),
    }
    for name,(args,result) in specs.items():
        fn=getattr(k,name);fn.argtypes=args;fn.restype=result
    return k


def require(value):
    if not value:raise ctypes.WinError(ctypes.get_last_error())
    return value


def join_request_job(name):
    """Fail closed BEFORE load/inference if the owner/job has disappeared."""
    if not name or not name.startswith('Local\\property-ai-request-'):
        raise RuntimeError('REQUEST_JOB_REQUIRED')
    k=kernel();handle=require(k.OpenJobObjectW(1,False,name))
    try:require(k.AssignProcessToJobObject(handle,k.GetCurrentProcess()))
    finally:k.CloseHandle(handle)  # Only the supervisor retains a job handle.


class RequestJob:
    def __init__(self):
        self.k=kernel();self.name='Local\\property-ai-request-'+uuid4().hex
        self.handle=require(self.k.CreateJobObjectW(None,self.name))
        limits=ExtendedLimits();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
        try:require(self.k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)))
        except BaseException:self.close();raise

    def active(self):
        result=Accounting()
        require(self.k.QueryInformationJobObject(self.handle,1,ctypes.byref(result),ctypes.sizeof(result),None))
        return result.active

    def terminate_and_confirm(self,seconds=10):
        require(self.k.TerminateJobObject(self.handle,1))
        end=time.monotonic()+seconds
        while self.active():
            if time.monotonic()>=end:raise RuntimeError('REQUEST_DESCENDANTS_EXIT_UNKNOWN')
            time.sleep(.05)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle);self.handle=None
